"""Business codes OPX-2026-0001, CPX-…, HD-…, TT-… — never reused, never changed."""
from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import SpendSequence

PREFIXES = {"opex": "OPX", "capex": "CPX", "contract": "HD", "proposal": "TT", "quote": "BG", "renovation": "CTVP"}


def next_code(db: Session, kind: str, year: int) -> str:
    """Reserve the next number inside the caller's transaction. The UPDATE takes a row lock on
    PostgreSQL (SQLite locks the database), so two concurrent creations get different numbers;
    the UNIQUE code column turns any other collision into a hard error."""
    prefix = PREFIXES[kind]
    res = db.execute(update(SpendSequence).where(SpendSequence.kind == prefix, SpendSequence.year == year)
                     .values(last_no=SpendSequence.last_no + 1))
    if res.rowcount == 0:
        db.add(SpendSequence(kind=prefix, year=year, last_no=1))
        db.flush()
        no = 1
    else:
        no = db.query(SpendSequence.last_no).filter_by(kind=prefix, year=year).scalar()
    return f"{prefix}-{year}-{no:04d}"
