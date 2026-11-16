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


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """Every test starts with a clean failed-login counter (app/login_guard.py)."""
    from app import login_guard
    login_guard.reset()
    yield
