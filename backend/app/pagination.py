"""Offset / limit paging for list endpoints (audit API-05).

The JSON body keeps the shape the frontend already reads (a list, or the existing object);
the size of the whole filtered set goes in response headers instead:
  X-Total-Count  rows matching the filters (before offset / limit)
  X-Has-More     "true" when rows remain after this page
Callers must order the query deterministically (with an id tiebreak) before paging."""
from typing import Any, List

from fastapi import Response

TOTAL_HEADER = "X-Total-Count"
HAS_MORE_HEADER = "X-Has-More"


def set_page_headers(response: Response, total: int, offset: int, returned: int) -> None:
    response.headers[TOTAL_HEADER] = str(int(total))
    response.headers[HAS_MORE_HEADER] = "true" if offset + returned < total else "false"


def paginate(query, response: Response, offset: int, limit: int) -> List[Any]:
    """Run an ordered SQLAlchemy query for one page and set the paging headers.
    The total is a COUNT over the same filters without ORDER BY."""
    total = query.order_by(None).count()
    rows = query.offset(offset).limit(limit).all()
    set_page_headers(response, total, offset, len(rows))
    return rows
