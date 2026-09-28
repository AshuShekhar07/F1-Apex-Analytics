"""Team reliability: probability a car does not finish, from its team's recent races.

p_dnf(team, before date) = (team non-finishes + K * field rate) / (team starts + K)

  team: its last WINDOW races strictly before the date (any era; teams keep their
        reliability habits across rule changes better than their pace)
  field rate: non-finish share over all cars in the last FIELD_WINDOW races
  K: shrink toward the field rate, so one bad weekend does not condemn a team

WINDOW, FIELD_WINDOW and K are fixed a priori, not tuned on scored races.
"Did not finish" = not classified (race_status.is_classified).

Used by the scoreboard candidate blend_dnf: rank drivers by expected finishing
position, (1 - p_dnf) * blend rank + p_dnf * (field + 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from sqlalchemy import text

WINDOW = 10          # team races
FIELD_WINDOW = 20    # races for the field-wide rate
K = 20.0             # starts of field-rate evidence the team rate is shrunk toward


@dataclass(frozen=True)
class Start:
    race_date: date
    race_id: int
    team_id: int
    finished: bool


def field_rate(history: Sequence[Start], before: date, window: int = FIELD_WINDOW) -> float:
    races = sorted({(s.race_date, s.race_id) for s in history if s.race_date < before})[-window:]
    keep = set(races)
    starts = [s for s in history if (s.race_date, s.race_id) in keep]
    return sum(not s.finished for s in starts) / len(starts) if starts else 0.15


def team_dnf_probability(history: Sequence[Start], team_id: int, before: date, *, base: float,
                         window: int = WINDOW, k: float = K) -> float:
    team = [s for s in history if s.team_id == team_id and s.race_date < before]
    races = set(sorted({(s.race_date, s.race_id) for s in team})[-window:])
    recent = [s for s in team if (s.race_date, s.race_id) in races]
    dnfs = sum(not s.finished for s in recent)
    return (dnfs + k * base) / (len(recent) + k)


def race_probabilities(history: Sequence[Start], race_date: date, team_ids: Sequence[int]) -> dict[int, float]:
    base = field_rate(history, race_date)
    return {t: team_dnf_probability(history, t, race_date, base=base) for t in set(team_ids)}


def expected_position_ranks(blend_ranks: Sequence[int], p_dnf: Sequence[float]) -> list[int]:
    """Re-rank by expected finish: a retirement is classified behind every finisher."""
    from race_form_signal_v1 import ranks

    field = len(blend_ranks)
    return ranks([(1 - p) * r + p * (field + 1) for r, p in zip(blend_ranks, p_dnf)])


def load_starts(db: Any) -> list[Start]:
    from race_status import is_classified

    rows = db.execute(text("""
        SELECT r.race_date, r.id, re.team_id, rr.status
        FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
        JOIN races r ON r.id = s.race_id
        JOIN race_entries re ON re.id = rr.race_entry_id
        WHERE rr.finishing_position IS NOT NULL AND re.team_id IS NOT NULL
    """)).all()
    return [Start(d, int(i), int(t), is_classified(st)) for d, i, t, st in rows]

