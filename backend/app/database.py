from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def ensure_schema() -> None:
    """create_all() plus tiny additive migrations for columns introduced after
    the first release (SQLAlchemy's create_all never alters existing tables).
    Safe to call on every start — each ALTER runs only when the column is
    missing, on both SQLite and PostgreSQL."""
    import logging
    import time

    from sqlalchemy import inspect, text
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    # Two app processes starting at once on an empty database (e.g. old and new pod
    # during a rollout) race on CREATE TABLE; the loser sees "already exists" —
    # wait briefly and retry: create_all skips tables that now exist.
    for attempt in range(5):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except (IntegrityError, ProgrammingError):
            if attempt == 4:
                raise
            time.sleep(2)
    additive = {
        "vm_snapshots": [
            ("instance_uuid", "VARCHAR(64)"),
            ("guest_id", "VARCHAR(64)"), ("guest_full_name", "VARCHAR(255)"),
            ("tools_guest_full_name", "VARCHAR(255)"), ("tools_guest_family", "VARCHAR(64)"),
            ("tools_status", "VARCHAR(32)"), ("guest_hostname", "VARCHAR(255)"),
        ],
        "host_snapshots": [
            ("vendor", "VARCHAR(128)"), ("model", "VARCHAR(128)"), ("esxi_version", "VARCHAR(64)"),
            ("esxi_build", "VARCHAR(64)"), ("cpu_model", "VARCHAR(255)"), ("cpu_sockets", "INTEGER"),
            ("cpu_threads", "INTEGER"), ("cluster_ha", "BOOLEAN"), ("cluster_drs", "BOOLEAN"),
        ],
        "license_entitlements": [
            ("kind", "VARCHAR(16) NOT NULL DEFAULT 'owned'"),
        ],
        "spend_vendors": [
            ("short_name", "VARCHAR(64)"), ("aliases", "TEXT"), ("address", "VARCHAR(500)"),
            ("contact_name", "VARCHAR(128)"), ("contact_email", "VARCHAR(255)"), ("contact_phone", "VARCHAR(64)"),
            ("website", "VARCHAR(255)"), ("tags", "VARCHAR(500)"), ("notes", "TEXT"),
            ("status", "VARCHAR(12) DEFAULT 'active'"), ("status_reason", "VARCHAR(500)"),
        ],
        "quote_requests": [("renovation_project_id", "INTEGER REFERENCES renovation_projects(id)"),
                           ("ai_summary", "TEXT"), ("ai_summary_at", "TIMESTAMP"), ("ai_summary_by", "VARCHAR(255)")],
        "proposals": [("quote_request_id", "INTEGER REFERENCES quote_requests(id)")],
        "spend_notify_channels": [("ops_alerts", "BOOLEAN DEFAULT FALSE")],
        "ops_alert_state": [("source", "VARCHAR(16)"), ("sent_channels", "TEXT")],
        "spend_ai_config": [("tasks_json", "TEXT"), ("mask_names", "BOOLEAN DEFAULT TRUE"), ("budget_usd", "INTEGER DEFAULT 100"),
                            ("alert_usd", "INTEGER DEFAULT 50"), ("alert_month", "VARCHAR(7)"), ("rate_per_hour", "INTEGER DEFAULT 30")],
        "quotation_lines": [("cost_group_id", "INTEGER REFERENCES renovation_cost_groups(id) ON DELETE SET NULL")],
        "users": [
            ("auth_source", "VARCHAR(16) NOT NULL DEFAULT 'local'"), ("external_id", "VARCHAR(64)"),
            ("email", "VARCHAR(255)"), ("last_login_at", "TIMESTAMP"),
            ("is_admin", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("spend_role", "VARCHAR(16) NOT NULL DEFAULT 'none'"), ("spend_role_source", "VARCHAR(8)"),
            ("session_version", "INTEGER NOT NULL DEFAULT 0"),
        ],
    }
    indexed = {"instance_uuid", "external_id", "renovation_project_id", "quote_request_id"}
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in additive.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    if name in indexed:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{name} ON {table} ({name})"))
        # Composite indexes for the hot history queries (audit QRY-03):
        # * vm_snapshots: history.py filters vcenter_key = ? AND captured_at >= ? (VM changes window);
        # * snapshot_runs: data_access.latest_run_for_key / scheduler._enrich_activity filter
        #   vcenter_key, status, source and take the newest finished_at.
        # Plain CREATE INDEX on purpose: ensure_schema runs inside engine.begin() and PostgreSQL
        # refuses CREATE INDEX CONCURRENTLY in a transaction. On a large existing vm_snapshots table
        # the first start blocks writes to it while the index builds (reads are unaffected); for a
        # very large table create it beforehand by hand with CONCURRENTLY and the same name —
        # IF NOT EXISTS then makes this a no-op.
        for name, table, cols in (
            ("ix_vm_snapshots_vc_captured", "vm_snapshots", "vcenter_key, captured_at"),
            ("ix_snapshot_runs_latest", "snapshot_runs", "vcenter_key, status, source, finished_at"),
        ):
            if table in insp.get_table_names():
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})"))
        # NULL in a flag that has a safe default is read as that default by the code, but backfill it so
        # every reader agrees (SCH-08). Idempotent; matches no row after the first run.
        for table, col, val in (("spend_ai_config", "mask_names", "TRUE"), ("spend_notify_channels", "ops_alerts", "FALSE"),
                                ("spend_vendors", "status", "'active'")):
            if table in insp.get_table_names() and col in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"UPDATE {table} SET {col} = {val} WHERE {col} IS NULL"))
        # One short name per vendor (used in every comparison / evaluation document and in tờ trình);
        # partial unique index on the lower-cased value works on SQLite and PostgreSQL.
        if "spend_vendors" in insp.get_table_names():
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_spend_vendor_short_name "
                              "ON spend_vendors (lower(short_name)) WHERE short_name IS NOT NULL"))
        # One OS override per VM (by uuid, or by name when the uuid is unknown). COALESCE because
        # NULLs never collide in a unique index. Created only when the existing rows are already
        # unique: duplicates are left for the newest-wins read path, never deleted at startup.
        if "vm_os_overrides" in insp.get_table_names():
            for name, cols, where in (
                ("uq_vm_os_override_uuid", "COALESCE(vcenter_key, ''), instance_uuid", "instance_uuid IS NOT NULL"),
                ("uq_vm_os_override_name", "COALESCE(vcenter_key, ''), vm", "instance_uuid IS NULL"),
            ):
                dup = conn.execute(text(f"SELECT 1 FROM vm_os_overrides WHERE {where} "
                                        f"GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 1")).first()
                if dup is None:
                    conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON vm_os_overrides ({cols}) WHERE {where}"))
                else:
                    logging.getLogger("vco.database").warning(
                        "vm_os_overrides has duplicate rows; %s not created (newest row is used)", name)
        # Entra user principal names can be longer than the original 64-character username column.
        if engine.dialect.name == "postgresql" and "users" in insp.get_table_names():
            col = next((c for c in insp.get_columns("users") if c["name"] == "username"), None)
            if col is not None and getattr(col["type"], "length", None) and col["type"].length < 255:
                conn.execute(text("ALTER TABLE users ALTER COLUMN username TYPE VARCHAR(255)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
