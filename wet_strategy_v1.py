from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

import os
from sqlalchemy import create_engine, text


ENGINE = create_engine(os.environ["DATABASE_URL"])

WET = {"INTERMEDIATE", "WET"}


def era_for_year(year: int) -> str:
    if year <= 2021:
        return "era1_13inch"
    if year <= 2025:
        return "era2_18inch_groundeffect"
    return "era3_2026regs"


def regime(race: dict[str, Any]) -> str:
    seq = [s["compound"] for s in race["stints"]]
    onset = race["rain_onset_lap"]

    transitions = sum(
        seq[i] != seq[i - 1]
        for i in range(1, len(seq))
    )
    wet_count = sum(c in WET for c in seq)

    if "WET" in seq or (wet_count >= 3 and transitions >= 3):
        return "EXTREME_WET"

    if onset is None:
        return "UNKNOWN"

    if onset <= 10:
        return "EARLY_WET"
    if onset <= 40:
        return "MID_TRANSITION"
    return "LATE_WET"


def onset_bucket(lap: int | None) -> str:
    if lap is None:
        return "unknown"
    if lap <= 10:
        return "early"
    if lap <= 40:
        return "mid"
    return "late"


def load_wet_races(
    conn,
    target_date: date,
    eras: list[str],
) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        text("""
            SELECT
                r.id AS race_id,
                r.track_id,
                r.season_year,
                r.round_number,
                r.race_date,
                r.regulation_era,
                t.name AS track_name,
                sw.rain_onset_lap,
                rs.stint_number,
                rs.compound,
                rs.start_lap,
                rs.end_lap
            FROM races r
            JOIN tracks t
              ON t.id = r.track_id
            JOIN sessions s
              ON s.race_id = r.id
             AND s.session_type = 'R'
            JOIN session_weather sw
              ON sw.session_id = s.id
            JOIN race_stints rs
              ON rs.race_id = r.id
             AND rs.finishing_position = 1
            JOIN race_results rr
              ON rr.race_entry_id = rs.race_entry_id
             AND rr.session_id = s.id
            WHERE r.race_date < :target_date
              AND r.regulation_era = ANY(:eras)
              AND sw.rainfall = TRUE
              AND rr.status IN (
                  'Finished',
                  '+1 Lap',
                  '+2 Laps',
                  '+3 Laps',
                  '+4 Laps',
                  '+5 Laps',
                  '+6 Laps'
              )
            ORDER BY r.race_date, rs.stint_number
        """),
        {
            "target_date": target_date,
            "eras": eras,
        },
    ).mappings().all()

    races: dict[int, dict[str, Any]] = {}

    for r in rows:
        rid = r["race_id"]

        if rid not in races:
            races[rid] = {
                "race_id": rid,
                "track_id": r["track_id"],
                "track_name": r["track_name"],
                "season_year": r["season_year"],
                "round_number": r["round_number"],
                "race_date": r["race_date"],
                "regulation_era": r["regulation_era"],
                "rain_onset_lap": r["rain_onset_lap"],
                "stints": [],
            }

        races[rid]["stints"].append({
            "compound": str(r["compound"]).upper(),
            "start": r["start_lap"],
            "end": r["end_lap"],
        })

    return {
        rid: race
        for rid, race in races.items()
        if any(s["compound"] in WET for s in race["stints"])
    }


def score(
    race: dict[str, Any],
    target_track_id: int,
    target_onset: int | None,
    target_date: date,
) -> float:
    value = 0.0

    if race["track_id"] == target_track_id:
        value += 5.0

    if onset_bucket(race["rain_onset_lap"]) == onset_bucket(target_onset):
        value += 4.0

    if target_onset is not None and race["rain_onset_lap"] is not None:
        delta = abs(race["rain_onset_lap"] - target_onset)
        value += max(0.0, 3.0 - delta / 15.0)

    age_years = max(
        0.0,
        (target_date - race["race_date"]).days / 365.25,
    )
    value += max(0.0, 2.0 - 0.4 * age_years)

    return value


def predict_wet_strategy(
    track_id: int,
    race_date: date,
    *,
    rain_onset_lap: int | None,
    regulation_era: str | None = None,
) -> dict[str, Any]:

    if regulation_era is None:
        regulation_era = era_for_year(race_date.year)

    if rain_onset_lap is None:
        target_regime = "EARLY_WET"
    elif rain_onset_lap <= 10:
        target_regime = "EARLY_WET"
    elif rain_onset_lap <= 40:
        target_regime = "MID_TRANSITION"
    else:
        target_regime = "LATE_WET"

    previous_era = {
        "era3_2026regs": "era2_18inch_groundeffect",
        "era2_18inch_groundeffect": "era1_13inch",
        "era1_13inch": None,
    }.get(regulation_era)

    with ENGINE.connect() as conn:
        same_era = load_wet_races(
            conn,
            race_date,
            [regulation_era],
        )

        # 1. Same era + same regime.
        candidates = [
            r for r in same_era.values()
            if regime(r) == target_regime
        ]
        evidence_scope = "same-era, same-regime"
        confidence_penalty = 0

        # 2. Same era + all genuine wet races.
        if not candidates:
            candidates = list(same_era.values())
            evidence_scope = "same-era, all-genuine-wet"
            confidence_penalty = 10

        # 3/4. Prior era fallback.
        if not candidates and previous_era:
            prior = load_wet_races(
                conn,
                race_date,
                [previous_era],
            )

            candidates = [
                r for r in prior.values()
                if regime(r) == target_regime
            ]

            if candidates:
                evidence_scope = "prior-era, same-regime"
                confidence_penalty = 20
            else:
                candidates = list(prior.values())
                evidence_scope = "prior-era, all-genuine-wet"
                confidence_penalty = 25

    if not candidates:
        return {
            "status": "insufficient_history",
            "model": "wet_strategy_v1",
            "regulation_era": regulation_era,
            "regime": target_regime,
            "rain_onset_lap": rain_onset_lap,
            "evidence_scope": evidence_scope,
            "confidence": 0,
            "primary_strategy": None,
            "alternatives": [],
        }

    scored = []

    for race in candidates:
        seq = tuple(s["compound"] for s in race["stints"])
        scored.append((
            score(
                race,
                track_id,
                rain_onset_lap,
                race_date,
            ),
            race,
            seq,
        ))

    scored.sort(key=lambda x: x[0], reverse=True)

    sequence_scores = Counter()

    for race_score, _, seq in scored:
        sequence_scores[seq] += race_score

    ranked = sequence_scores.most_common(4)
    total = sum(v for _, v in ranked) or 1.0

    options = []

    for seq, seq_score in ranked:
        support = []

        for _, race, race_seq in scored:
            if race_seq == seq:
                support.append({
                    "race_id": race["race_id"],
                    "season_year": race["season_year"],
                    "round_number": race["round_number"],
                    "track_name": race["track_name"],
                    "rain_onset_lap": race["rain_onset_lap"],
                    "strategy": race["stints"],
                })

        options.append({
            "strategy": list(seq),
            "score": round(seq_score, 4),
            "evidence_share": round(seq_score / total, 4),
            "historical_races": len(support),
            "supporting_races": support[:5],
        })

    primary = options[0]

    # Conservative confidence:
    # evidence share + sample size, reduced for fallback evidence.
    confidence = int(round(primary["evidence_share"] * 100))

    if primary["historical_races"] >= 3:
        confidence += 10
    elif primary["historical_races"] == 2:
        confidence += 5

    confidence -= confidence_penalty
    confidence = max(20, min(75, confidence))

    return {
        "status": "ok",
        "model": "wet_strategy_v1",
        "regulation_era": regulation_era,
        "regime": target_regime,
        "rain_onset_lap": rain_onset_lap,
        "evidence_scope": evidence_scope,
        "confidence": confidence,
        "primary_strategy": primary,
        "alternatives": options[1:],
    }


def validate(race_id: int):
    with ENGINE.connect() as conn:
        target = conn.execute(
            text("""
                SELECT
                    r.id,
                    r.track_id,
                    r.race_date,
                    r.regulation_era,
                    sw.rain_onset_lap
                FROM races r
                JOIN sessions s
                  ON s.race_id = r.id
                 AND s.session_type = 'R'
                JOIN session_weather sw
                  ON sw.session_id = s.id
                WHERE r.id = :race_id
                LIMIT 1
            """),
            {"race_id": race_id},
        ).mappings().first()

        actual_rows = conn.execute(
            text("""
                SELECT
                    rs.stint_number,
                    rs.compound,
                    rs.start_lap,
                    rs.end_lap
                FROM race_stints rs
                JOIN sessions s
                  ON s.race_id = rs.race_id
                 AND s.session_type = 'R'
                JOIN race_results rr
                  ON rr.race_entry_id = rs.race_entry_id
                 AND rr.session_id = s.id
                WHERE rs.race_id = :race_id
                  AND rs.finishing_position = 1
                ORDER BY rs.stint_number
            """),
            {"race_id": race_id},
        ).mappings().all()

    if not target:
        raise ValueError(f"Race {race_id} not found")

    actual = tuple(
        str(x["compound"]).upper()
        for x in actual_rows
    )

    # Historical validation must use the race's REAL weather.
    result = predict_wet_strategy(
        target["track_id"],
        target["race_date"],
        rain_onset_lap=target["rain_onset_lap"],
        regulation_era=target["regulation_era"],
    )

    if not result["primary_strategy"]:
        return {
            "race_id": race_id,
            "actual": actual,
            "primary": None,
            "exact": False,
            "top3": False,
            "regime": result["regime"],
            "evidence_scope": result["evidence_scope"],
        }

    ranked = [
        tuple(result["primary_strategy"]["strategy"])
    ]
    ranked += [
        tuple(x["strategy"])
        for x in result["alternatives"]
    ]

    return {
        "race_id": race_id,
        "regime": result["regime"],
        "evidence_scope": result["evidence_scope"],
        "confidence": result["confidence"],
        "actual": actual,
        "primary": ranked[0],
        "exact": actual == ranked[0],
        "top3": actual in ranked[:3],
        "ranked": ranked[:3],
    }


if __name__ == "__main__":
    print("================================")
    print("WET STRATEGY V1 — SANITY CHECK")
    print("================================")

    for race_id in [137, 150, 134, 109, 88]:
        try:
            r = validate(race_id)

            print(
                r["race_id"],
                "regime=", r["regime"],
                "scope=", r["evidence_scope"],
                "confidence=", r.get("confidence"),
                "actual=", " -> ".join(r["actual"]),
                "primary=",
                " -> ".join(r["primary"]) if r["primary"] else None,
                "exact=", r["exact"],
                "top3=", r["top3"],
                "ranked=", r.get("ranked"),
            )
        except Exception as e:
            print(race_id, "ERROR:", repr(e))
