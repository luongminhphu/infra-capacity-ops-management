"""GET /api/exports — the standard Export menu: every export the signed-in user may run now
(module switched on, role held), grouped by module."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import exports
from ..database import get_db
from ..features import get_features
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["exports"])


@router.get("/exports")
def list_exports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    feats = get_features(db)
    return {"items": [exports.public(e) for e in exports.EXPORTS if exports.allowed(e, user, feats)]}
