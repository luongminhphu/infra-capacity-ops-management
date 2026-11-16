"""Validation of JSON / CSV kept in Text columns (audit SCH-10).

The database does not check these columns (rule 4: their type stays Text), so a broken value used
to surface far from the code that wrote it — in a report, a scheduled job, a page that no longer
loads. `register()` attaches an attribute "set" listener: every ORM assignment (constructor
keyword or `row.col = ...`) is checked at once and an `InvalidStoredValue` is raised in the
writer's stack. `main.py` turns it into HTTP 422 with the Vietnamese message.

Bulk INSERTs (`insert()` / `bulk_insert_mappings`) bypass ORM events; their writers build the value
with json.dumps on a known structure (backup collector) and are covered by tests instead.
"""
import json
import re
from typing import Any, Callable, Optional, Tuple, Type, Union

from sqlalchemy import event

JsonKind = Union[Type[dict], Type[list], Tuple[type, ...], None]


class InvalidStoredValue(ValueError):
    """A JSON / CSV text value of the wrong shape. `message` is Vietnamese and safe to show."""

    def __init__(self, label: str, message: str):
        self.label = label
        self.message = f"{label}: {message}"
        super().__init__(self.message)


_KIND_TXT = {dict: "đối tượng JSON ({…})", list: "danh sách JSON ([…])"}


def check_json(value: Any, kind: JsonKind, label: str, nullable: bool = True) -> Optional[str]:
    """Return the value unchanged when it is a JSON text of the expected kind (None when nullable)."""
    if value is None:
        if nullable:
            return None
        raise InvalidStoredValue(label, "không được để trống.")
    if not isinstance(value, str):
        raise InvalidStoredValue(label, "phải là chuỗi JSON (code chưa json.dumps giá trị).")
    try:
        parsed = json.loads(value)
    except ValueError:
        raise InvalidStoredValue(label, "không phải JSON hợp lệ.") from None
    if kind is not None and not isinstance(parsed, kind):
        want = _KIND_TXT.get(kind, "JSON đúng kiểu") if isinstance(kind, type) else "JSON đúng kiểu"
        raise InvalidStoredValue(label, f"phải là {want}.")
    return value


def check_csv(value: Any, label: str, item: Optional[str] = None, nullable: bool = True,
              allow_empty: bool = True) -> Optional[str]:
    """Comma-separated text; each non-blank item must fully match regex `item` when given."""
    if value is None:
        if nullable:
            return None
        raise InvalidStoredValue(label, "không được để trống.")
    if not isinstance(value, str):
        raise InvalidStoredValue(label, "phải là chuỗi phân tách bằng dấu phẩy.")
    parts = [p.strip() for p in value.split(",")]
    if any(p == "" for p in parts) and value.strip() != "":
        raise InvalidStoredValue(label, "có phần tử rỗng giữa hai dấu phẩy.")
    if not allow_empty and value.strip() == "":
        raise InvalidStoredValue(label, "không được để trống.")
    if item:
        rx = re.compile(item)
        bad = [p for p in parts if p and not rx.fullmatch(p)]
        if bad:
            raise InvalidStoredValue(label, f"giá trị “{bad[0][:40]}” không hợp lệ.")
    return value


def register(attr, check: Callable[[Any], Any]) -> None:
    """Run `check(value)` on every ORM assignment to `attr` (an InstrumentedAttribute)."""
    @event.listens_for(attr, "set", retval=True)
    def _validate(target, value, oldvalue, initiator):  # noqa: ANN001
        return check(value)


def json_column(attr, kind: JsonKind, label: str, nullable: bool = True) -> None:
    register(attr, lambda v: check_json(v, kind, label, nullable))


def csv_column(attr, label: str, item: Optional[str] = None, nullable: bool = True) -> None:
    register(attr, lambda v: check_csv(v, label, item, nullable))
