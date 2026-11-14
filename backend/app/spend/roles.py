"""Roles of the spend module: none < viewer < editor < approver; admins have every right.

Checked at the endpoint (dependency), never only by hiding buttons in the UI."""
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, status

from ..config import Settings
from ..models import User
from ..security import get_current_user, is_admin

ROLES = ("none", "viewer", "editor", "approver")
RANK = {r: i for i, r in enumerate(ROLES)}
LABELS = {"none": "Không có quyền", "viewer": "Xem", "editor": "Biên tập", "approver": "Duyệt", "admin": "Quản trị"}


def effective_role(user: User) -> str:
    """'admin' for administrators, otherwise the stored spend role."""
    if is_admin(user):
        return "admin"
    role = user.spend_role or "none"
    return role if role in RANK else "none"


def has_role(user: User, minimum: str) -> bool:
    role = effective_role(user)
    if role == "admin":
        return True
    if minimum == "admin":
        return False
    return RANK[role] >= RANK[minimum]


def require_spend(minimum: str = "viewer"):
    if minimum not in RANK and minimum != "admin":
        raise ValueError(minimum)

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_role(user, minimum):
            need = LABELS[minimum]
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Cần quyền “{need}” trong module Chi phí & Hợp đồng.")
        return user
    return _dep


def _csv(value: str) -> set:
    return {x.strip() for x in (value or "").split(",") if x.strip()}


def role_from_entra(s: Settings, token_roles: Iterable[str]) -> Optional[str]:
    """Highest spend role granted by the token's app roles, or None when it carries none."""
    roles = set(token_roles or [])
    for name, setting in (("approver", s.entra_spend_approver_roles), ("editor", s.entra_spend_editor_roles),
                          ("viewer", s.entra_spend_viewer_roles)):
        if roles & _csv(setting):
            return name
    return None


def apply_entra_roles(user: User, s: Settings, token_roles: Iterable[str]) -> None:
    """Entra app role wins; when the token has none, a role an admin set in the UI is kept,
    but a role that came from Entra earlier is withdrawn (app role removed)."""
    granted = role_from_entra(s, token_roles)
    if granted:
        user.spend_role, user.spend_role_source = granted, "entra"
    elif user.spend_role_source == "entra":
        user.spend_role, user.spend_role_source = "none", None
