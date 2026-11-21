"""The four status levels of ICO (MASTER: AN TOÀN / THEO DÕI / CẢNH BÁO / VƯỢT NGƯỠNG) — one definition for the
infra / hardware services and every PDF / Excel that prints a level."""

LEVEL_RANK = {"crit": 0, "bad": 1, "warn": 2, "ok": 3}
LEVEL_TEXT = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}


def worst(*levels: str) -> str:
    """Most severe of the given levels (unknown levels rank last)."""
    return min(levels, key=lambda lv: LEVEL_RANK.get(lv, 9))
