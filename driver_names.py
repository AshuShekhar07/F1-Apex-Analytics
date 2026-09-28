"""One rule for "is this the same driver name?", shared by every loader.

FastF1 has switched between plain and accented spellings over time ("Sergio Perez" /
"Sergio Pérez", "Nico Hulkenberg" / "Nico Hülkenberg"). Matching on the exact string
created duplicate drivers, so names are compared after removing accents, case and
repeated spaces. The spelling already stored in the database is kept.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import text


def normalise_name(name: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.casefold().split())


def find_driver_id(conn: Any, name: str | None) -> int | None:
    """Existing driver whose name matches ignoring accents/case (lowest id if several)."""
    key = normalise_name(name)
    if not key:
        return None
    matches = [int(i) for i, n in conn.execute(text("SELECT id, name FROM drivers ORDER BY id"))
               if normalise_name(n) == key]
    return matches[0] if matches else None


def canonical_driver_name(conn: Any, name: str | None) -> str | None:
    """The spelling stored in the database for this driver, or the given name if new."""
    driver_id = find_driver_id(conn, name)
    if driver_id is None:
        return name
    return conn.execute(text("SELECT name FROM drivers WHERE id = :i"), {"i": driver_id}).scalar()
