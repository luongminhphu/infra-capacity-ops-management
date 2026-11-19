"""Owner portal access (ico-v2-plan.md §7). get_owner_user admits both scopes and carries the systems the caller may
see: an owner-scope user only the systems whose owner_email or deputy_email is their own e-mail / UPN; a full user all.

Ownership is decided here, server side, from the signed-in account — never from a request parameter."""
from dataclasses import dataclass
from typing import Optional, Set

from fastapi import Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import get_session_user
from .models import System


@dataclass
class OwnerPrincipal:
    user: User
    system_ids: Optional[Set[int]]           # None = every system (full access)

    def allows(self, system_id: int) -> bool:
        return self.system_ids is None or system_id in self.system_ids


def identities(user: User) -> Set[str]:
    """E-mail addresses that name this user as owner / deputy (Entra: UPN and mail, lower case)."""
    return {x.strip().lower() for x in (user.email, user.username) if x and "@" in x}


def owned_system_ids(db: Session, user: User) -> Set[int]:
    ids = identities(user)
    if not ids:
        return set()
    rows = db.query(System.id).filter(System.status == "active",
                                      or_(func.lower(System.owner_email).in_(ids), func.lower(System.deputy_email).in_(ids)))
    return {i for (i,) in rows}


def get_owner_user(user: User = Depends(get_session_user), db: Session = Depends(get_db)) -> OwnerPrincipal:
    if (user.access_scope or "full") == "full":
        return OwnerPrincipal(user, None)
    return OwnerPrincipal(user, owned_system_ids(db, user))
