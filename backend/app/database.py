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
            ("folder_path", "VARCHAR(512)"), ("resource_pool", "VARCHAR(255)"), ("custom_attrs", "TEXT"),
        ],
        "host_snapshots": [
            ("vendor", "VARCHAR(128)"), ("model", "VARCHAR(128)"), ("esxi_version", "VARCHAR(64)"),
            ("esxi_build", "VARCHAR(64)"), ("cpu_model", "VARCHAR(255)"), ("cpu_sockets", "INTEGER"),
            ("cpu_threads", "INTEGER"), ("cluster_ha", "BOOLEAN"), ("cluster_drs", "BOOLEAN"),
            ("serial", "VARCHAR(64)"), ("hw_uuid", "VARCHAR(64)"),
        ],
        "license_entitlements": [
            ("kind", "VARCHAR(16) NOT NULL DEFAULT 'owned'"), ("unit_price_minor", "BIGINT"),
        ],
        "ai_runs": [("cost_usd_micros", "BIGINT")],
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
        "budget_plan_lines": [("detail", "VARCHAR(1000)")],
        "dc_assets": [("disposed_on", "DATE"), ("exit_kind", "VARCHAR(16)")],
        "ops_alert_state": [("source", "VARCHAR(16)"), ("sent_channels", "TEXT")],
        "spend_ai_config": [("tasks_json", "TEXT"), ("mask_names", "BOOLEAN DEFAULT TRUE"), ("budget_usd", "INTEGER DEFAULT 100"),
                            ("alert_usd", "INTEGER DEFAULT 50"), ("alert_month", "VARCHAR(7)"), ("rate_per_hour", "INTEGER DEFAULT 30"),
                            ("reserved_micros", "BIGINT DEFAULT 0"), ("reserved_at", "TIMESTAMP")],
        "quotation_lines": [("cost_group_id", "INTEGER REFERENCES renovation_cost_groups(id) ON DELETE SET NULL"),
                            ("vat_pct", "NUMERIC(5, 2)"), ("vat_amount", "NUMERIC(18, 2)"), ("sets", "NUMERIC(10, 3)")],
        "datastore_states": [("backing", "TEXT")],
        "app_logs": [("request_id", "VARCHAR(64)"), ("pod", "VARCHAR(64)")],
        "ai_runs": [("request_id", "VARCHAR(64)"), ("prompt_version", "VARCHAR(32)"), ("feedback", "VARCHAR(4)")],
        "vc_alarm_states": [("entity_moid", "VARCHAR(64)"), ("vim_type", "VARCHAR(32)"), ("vc_uuid", "VARCHAR(64)")],
        "systems": [("deputy_name", "VARCHAR(128)"), ("support_emails", "TEXT"), ("support_name", "VARCHAR(128)"),
                    ("support_contact", "VARCHAR(255)")],
        "hw_source_state": [("inventory_at", "TIMESTAMP"), ("warranty_at", "TIMESTAMP"), ("alert_cursor", "TIMESTAMP"),
                            ("baseline_configured", "BOOLEAN"), ("last_warning", "TEXT")],
        "review_items": [("action_date", "DATE"), ("new_vcpu", "INTEGER"), ("new_ram_gb", "INTEGER"),
                         ("transfer_to", "VARCHAR(255)"), ("resolved_at", "TIMESTAMP"), ("resolved_by", "VARCHAR(255)")],
        "users": [
            ("auth_source", "VARCHAR(16) NOT NULL DEFAULT 'local'"), ("external_id", "VARCHAR(64)"),
            ("email", "VARCHAR(255)"), ("last_login_at", "TIMESTAMP"),
            ("is_admin", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("spend_role", "VARCHAR(16) NOT NULL DEFAULT 'none'"), ("spend_role_source", "VARCHAR(8)"),
            ("owner_admin", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("access_scope", "VARCHAR(8) NOT NULL DEFAULT 'full'"),
            ("session_version", "INTEGER NOT NULL DEFAULT 0"),
        ],
    }
    indexed = {"instance_uuid", "external_id", "renovation_project_id", "quote_request_id"}
    pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        if pg:
            # Old and new pod start together during a rollout (rule 4): one migrates at a time (SCH-02),
            # and a DDL waiting behind a long transaction gives up instead of queueing every query
            # behind its lock; the pod then restarts and tries again.
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        insp = inspect(conn)                     # read after the lock: the other pod may just have migrated
        for table, cols in additive.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {'IF NOT EXISTS ' if pg else ''}{name} {ddl}"))
                    if name in indexed:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{name} ON {table} ({name})"))
        # DAT-10 (money as integers). Release 1 added the integer twins and dual-wrote, release 2 switched
        # reads, release 3 (this code) no longer maps the Float columns at all. While they still exist:
        # copy any row an older pod wrote with only the Float (idempotent), and let rows be inserted without
        # them (ai_runs.cost_usd was NOT NULL). The columns are dropped when DROP_LEGACY_MONEY_COLUMNS=true —
        # set it on the deploy after this release, once no older pod runs (rule 4). SQLite (dev / tests,
        # one process) drops them right away.
        def _cols(t):
            return {c["name"]: c for c in insp.get_columns(t)} if t in insp.get_table_names() else {}
        lic, air = _cols("license_entitlements"), _cols("ai_runs")
        if "unit_price" in lic and "unit_price_minor" in lic:
            conn.execute(text("UPDATE license_entitlements SET unit_price_minor = "
                              "CASE WHEN upper(currency) IN ('VND', 'JPY', 'KRW') THEN ROUND(unit_price) ELSE ROUND(unit_price * 100) END "
                              "WHERE unit_price IS NOT NULL AND unit_price_minor IS NULL"))
        if "cost_usd" in air and "cost_usd_micros" in air:
            conn.execute(text("UPDATE ai_runs SET cost_usd_micros = ROUND(cost_usd * 1000000) "
                              "WHERE cost_usd IS NOT NULL AND cost_usd_micros IS NULL"))
            if pg and not air["cost_usd"].get("nullable", True):
                conn.execute(text("ALTER TABLE ai_runs ALTER COLUMN cost_usd DROP NOT NULL"))
        if not pg or get_settings().drop_legacy_money_columns:
            for t, c, have in (("license_entitlements", "unit_price", lic), ("ai_runs", "cost_usd", air)):
                if c in have:
                    conn.execute(text(f"ALTER TABLE {t} DROP COLUMN {'IF EXISTS ' if pg else ''}{c}"))
                    logging.getLogger("vco.database").info("DAT-10: dropped legacy Float column %s.%s", t, c)
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
    _ensure_indexes()
    _drop_retired_checks()
    _ensure_checks()


# CHECK constraints replaced by a wider one under a new name (the new one is added by _ensure_checks):
# dropping the old one never breaks a pod of the previous release, whose values the new one still allows.
RETIRED_CHECKS = (("budget_plan_lines", "ck_bpl_source"), ("dc_assets", "ck_dc_asset_decision"),
                  ("budget_plan_lines", "ck_bpl_source_v2"))


def _drop_retired_checks() -> None:
    """PostgreSQL only (SQLite keeps the constraint of the table it created: dev / test databases are
    recreated)."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import inspect, text
    tables = set(inspect(engine).get_table_names())
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("SET lock_timeout = '10s'"))
        for table, name in RETIRED_CHECKS:
            if table in tables:
                conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))


MIGRATION_LOCK_KEY = 7_410_316                 # arbitrary constant shared by every ICO pod
# Composite indexes for the hot history queries (audit QRY-03, SCH-05):
# * vm_snapshots: history.py filters vcenter_key = ? AND captured_at >= ? (VM changes window);
# * snapshot_runs: data_access.latest_run_for_key / scheduler._enrich_activity filter vcenter_key,
#   status, source and take the newest finished_at;
# * backup_jobs: purge and job window by server and start time.
BIG_INDEXES = (
    ("ix_vm_snapshots_vc_captured", "vm_snapshots", "vcenter_key, captured_at"),
    ("ix_snapshot_runs_latest", "snapshot_runs", "vcenter_key, status, source, finished_at"),
    ("ix_backup_jobs_nbu_start", "backup_jobs", "nbu_key, start_time"),
)


def _ensure_indexes() -> None:
    """Indexes declared on the models (Index(...), Column(index=True)) plus BIG_INDEXES, created when
    missing — create_all() never adds them to a table that already exists, so old databases drifted
    from new ones (SCH-04).

    PostgreSQL: CREATE INDEX CONCURRENTLY in autocommit mode, outside the startup transaction, so a
    build on a large table never blocks the collector's writes (SCH-03). A build interrupted earlier
    leaves an INVALID index: it is dropped and rebuilt. A unique index that cannot be built because
    of existing duplicates is logged and skipped, never fatal.
    SQLite: plain CREATE INDEX IF NOT EXISTS."""
    import logging

    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateIndex

    log = logging.getLogger("vco.database")
    pg = engine.dialect.name == "postgresql"
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    wanted = []                                  # (name, table, sql)
    for name, table, cols in BIG_INDEXES:
        if table in tables:
            wanted.append((name, table, f"CREATE INDEX {{c}}IF NOT EXISTS {name} ON {table} ({cols})"))
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        have = {i["name"] for i in insp.get_indexes(table.name)}
        for idx in table.indexes:
            if idx.name and idx.name not in have:
                ddl = str(CreateIndex(idx, if_not_exists=True).compile(dialect=engine.dialect))
                wanted.append((idx.name, table.name, ddl.replace("CREATE INDEX ", "CREATE INDEX {c}", 1)
                               .replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX {c}", 1)))
    if not wanted:
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for name, table, sql in wanted:
            try:
                if pg:
                    bad = conn.execute(text("SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                                            "WHERE c.relname = :n AND NOT i.indisvalid"), {"n": name}).first()
                    if bad:
                        conn.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
                conn.execute(text(sql.replace("{c}", "CONCURRENTLY " if pg else "")))
            except Exception as exc:  # noqa: BLE001 — a missing index must not stop the app
                log.warning("Index %s on %s not created (%s); the app works without it, only slower",
                            name, table, type(exc).__name__)


def _ensure_checks() -> None:
    """PostgreSQL only: CHECK constraints declared on the models but missing on a table created by an
    older release (SCH-04). Added NOT VALID (new rows are checked at once, no table scan under lock),
    then VALIDATE; old rows that break it are logged, never fatal. SQLite cannot add constraints to
    an existing table: it keeps the app-level validation."""
    if engine.dialect.name != "postgresql":
        return
    import logging

    from sqlalchemy import CheckConstraint, inspect, text

    log = logging.getLogger("vco.database")
    tables = set(inspect(engine).get_table_names())
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("SET lock_timeout = '10s'"))
        have = {r[0] for r in conn.execute(text("SELECT conname FROM pg_constraint WHERE contype = 'c'"))}
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            for ck in table.constraints:
                if not isinstance(ck, CheckConstraint) or not ck.name or ck.name in have:
                    continue
                expr = str(ck.sqltext.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
                try:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD CONSTRAINT {ck.name} CHECK ({expr}) NOT VALID'))
                except Exception as exc:  # noqa: BLE001
                    log.warning("CHECK %s on %s not added (%s)", ck.name, table.name, type(exc).__name__)
                    continue
                try:
                    conn.execute(text(f"ALTER TABLE {table.name} VALIDATE CONSTRAINT {ck.name}"))
                except Exception as exc:  # noqa: BLE001 — existing rows break it: new rows are still checked
                    log.warning("CHECK %s on %s added but old rows do not satisfy it (%s); fix them, then "
                                "ALTER TABLE ... VALIDATE CONSTRAINT", ck.name, table.name, type(exc).__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
