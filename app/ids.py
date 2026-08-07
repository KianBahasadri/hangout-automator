"""Row-id validation shared by the HTTP layer.

SQLite stores 64-bit signed integers, and binding a larger number raises
``OverflowError`` inside the driver. Without a bound, an id no row could ever
have crashes the lookup instead of simply missing, so client-supplied ids are
range-checked at the edge where the answer is a 422 (or a blank field) rather
than a 500.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import Field

MAX_ROW_ID = 2**63 - 1

# Row id in a URL, e.g. /api/profiles/{profile_id}.
RowIdPath = Annotated[int, Path(ge=1, le=MAX_ROW_ID)]

# Row id inside a JSON body or a form field.
RowId = Annotated[int, Field(ge=1, le=MAX_ROW_ID)]


def parse_row_id(value: str | None) -> int | None:
    """Row id from a free-text form field; None when blank, malformed, or out of range."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if 1 <= number <= MAX_ROW_ID else None
