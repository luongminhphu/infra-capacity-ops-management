"""Customer view of a sizing result, shared by every report (full PDF, compact PDF, Excel).

The web UI shows the person preparing the report how the result came about: the engine's
automatic pick, a manual override of option or host count, engine-suggested variants. A
consulting report sent to the customer must read as one consistent recommendation, so this
module removes that preparation trail:

* no "chosen instead of the automatic option" sentence — the chosen option *is* the recommendation;
* the recommended option is ranked #1 and the other candidates follow in their original order;
* suggested variants lose the "(gợi ý)" name suffix and are shown as plain reference options;
* the engine's own ranking explanation is dropped when it no longer describes the chosen option.
"""
import copy
from typing import Any, Dict

SUGGEST_SUFFIX = " (gợi ý)"


def customer_view(result: Dict[str, Any]) -> Dict[str, Any]:
    res = copy.deepcopy(result)
    sel = res.get("selection") or {}
    rows = res.get("results") or []

    for r in rows:
        name = (r.get("profile") or {}).get("name") or ""
        if name.endswith(SUGGEST_SUFFIX):
            r["profile"]["name"] = name[: -len(SUGGEST_SUFFIX)]

    a = res.get("analysis") or {}
    for f in a.get("findings") or []:
        for k in ("title", "detail", "action"):
            if isinstance(f.get(k), str):
                f[k] = f[k].replace(SUGGEST_SUFFIX, "")
    for k in ("expandText",):
        if isinstance(a.get(k), str):
            a[k] = a[k].replace(SUGGEST_SUFFIX, "")

    if sel.get("manual"):
        # The engine explanation compares the automatic pick with its peers; it would contradict
        # (or explain) the final choice. The preparer's own reason, if any, is printed separately.
        res.setdefault("recommendation", {})["why"] = ""
        best = next((r for r in rows if r.get("recommended")), None)
        ranked = sorted((r for r in rows if r.get("rank") is not None and r is not best), key=lambda r: r["rank"])
        if best is not None:
            best["rank"] = 1
            best["suggested"] = False
        for i, r in enumerate(ranked, start=2 if best is not None else 1):
            r["rank"] = i
    return res
