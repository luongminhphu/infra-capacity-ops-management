"""CI registry (sổ CI, ICO v2 G1): one identity per infrastructure object, rebuilt after each sync round.

models.py — tables ci / ci_key / ci_link; registry.py — set-based upserts; builder.py — build_all(db) from the
latest inventories; query.py — read API filtered by the caller's features and roles."""
