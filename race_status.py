"""Canonical race-result status taxonomy.

The values below are derived from the distinct ``race_results.status`` values
in the production database.  ``+4 Laps`` is retained as a valid classified
status even though it is not present in the current snapshot, because the
source format can emit any classified ``+N Lap`` result.
"""

from __future__ import annotations

from enum import Enum


class RaceStatusCategory(str, Enum):
    CLASSIFIED = "classified"
    DNF = "dnf"
    DNS = "dns"
    WITHDRAWN = "withdrawn"
    DISQUALIFIED = "disqualified"
    UNKNOWN = "unknown"


CLASSIFIED_STATUSES = (
    "Finished",
    "Lapped",
    "+1 Lap",
    "+2 Laps",
    "+3 Laps",
    "+4 Laps",
    "+5 Laps",
    "+6 Laps",
)
DNS_STATUSES = ("Did not start",)
WITHDRAWN_STATUSES = ("Withdrew",)
DISQUALIFIED_STATUSES = ("Disqualified",)

# Every observed terminal race-failure status.  This intentionally includes
# generic "Retired" alongside attributable mechanical and incident statuses.
DNF_STATUSES = (
    "Accident", "Battery", "Brakes", "Collision", "Collision damage",
    "Cooling system", "Damage", "Debris", "Differential", "Driveshaft",
    "Electrical", "Electronics", "Engine", "Exhaust", "Front wing",
    "Fuel leak", "Fuel pressure", "Fuel pump", "Gearbox", "Hydraulics",
    "Illness", "Mechanical", "Oil leak", "Out of fuel", "Overheating",
    "Power Unit", "Power loss", "Puncture", "Radiator", "Rear wing",
    "Retired", "Spun off", "Steering", "Suspension", "Transmission",
    "Turbo", "Tyre", "Undertray", "Vibrations", "Water leak",
    "Water pressure", "Water pump", "Wheel", "Wheel nut",
)

NON_RACE_STATUSES = DNS_STATUSES + WITHDRAWN_STATUSES + DISQUALIFIED_STATUSES

_CATEGORY_BY_STATUS = {
    **{status: RaceStatusCategory.CLASSIFIED for status in CLASSIFIED_STATUSES},
    **{status: RaceStatusCategory.DNF for status in DNF_STATUSES},
    **{status: RaceStatusCategory.DNS for status in DNS_STATUSES},
    **{status: RaceStatusCategory.WITHDRAWN for status in WITHDRAWN_STATUSES},
    **{status: RaceStatusCategory.DISQUALIFIED for status in DISQUALIFIED_STATUSES},
}


def classify_status(status: str | None) -> RaceStatusCategory:
    """Return the canonical category; absent/unrecognised values stay unknown."""
    return _CATEGORY_BY_STATUS.get(status, RaceStatusCategory.UNKNOWN)


def is_classified(status: str | None) -> bool:
    return classify_status(status) is RaceStatusCategory.CLASSIFIED


def is_dnf(status: str | None) -> bool:
    return classify_status(status) is RaceStatusCategory.DNF
