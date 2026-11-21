"""Small helpers for model declarations shared by every package (spend, owners, cmdb, hardware, events)."""


def in_check(col: str, values) -> str:
    """SQL text of a CHECK constraint: col IN ('a', 'b', …) — values are code constants, never user input."""
    return f"{col} IN ({', '.join(repr(v) for v in values)})"
