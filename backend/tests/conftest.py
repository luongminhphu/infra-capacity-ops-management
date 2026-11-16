"""Test environment: a throw-away SQLite database and one demo NetBackup server,
set before any app module creates the SQLAlchemy engine."""
import os
import tempfile

_db = os.path.join(tempfile.mkdtemp(prefix="ico-test-"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db}")
os.environ.setdefault("SPEND_FILES_DIR", os.path.join(os.path.dirname(_db), "files"))
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789")
os.environ.setdefault("ADMIN_PASSWORD", "Admin12345!")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ.setdefault("NETBACKUP_1_DEMO", "true")
os.environ.setdefault("NETBACKUP_1_KEY", "nbu-test")
os.environ.setdefault("NETBACKUP_1_HOST", "nbu-master.test.local")


import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app.database import engine as _engine  # noqa: E402


# SCH-11: SQLite leaves foreign keys unenforced unless every connection asks for it. PostgreSQL always
# enforces them, so the tests must too (RESTRICT / CASCADE, orphan rows).
@event.listens_for(_engine, "connect")
def _sqlite_fk_on(dbapi_conn, _record):
    if _engine.dialect.name == "sqlite":
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """Every test starts with a clean failed-login counter (app/login_guard.py)."""
    from app import login_guard
    login_guard.reset()
    yield


