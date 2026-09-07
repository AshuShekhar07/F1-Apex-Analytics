from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

import os
from sqlalchemy import create_engine, text


ENGINE = create_engine(os.environ["DATABASE_URL"])

WET = {"INTERMEDIATE", "WET"}
DRY_ERA2 = {"SOFT", "MEDIUM", "HARD"}
DRY_ERA1 = {"SOFT", "MEDIUM", "HARD", "ULTRASOFT", "SUPERSOFT", "HYPERSOFT"}


def load_targets(conn, target_date: date, era: str):
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
              AND r.regulation_era = :era
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
        {"target_date": target_date, "era": era},
    ).mappings().all()

    grouped = defaultdict(list)

    for row in rows:
        grouped[row["race_id"]].append(row)

    races = {}

    for rid, stints in grouped.items():
        compounds = [str(x["compound"]).upper() for x in stints]

        if not any(c in WET for c in compounds):
            continue

        onset = stints[0]["rain_onset_lap"]

        wet_idxs = [
            i for i, c in enumerate(compounds)
            if c in WET
        ]

        first_wet_i = wet_idxs[0]
        last_wet_i = wet_idxs[-1]

        first_wet = stints[first_wet_i]

        # Compound immediately before first wet stint.
        before = (
            stints[first_wet_i - 1]["compound"]
            if first_wet_i > 0
            else first_wet["compound"]
        )
        before = str(before).upper()

        # First dry compound after all wet stints.
        after = None
        for s in stints[last_wet_i + 1:]:
            c = str(s["compound"]).upper()
            if c not in WET:
                after = c
                break

        onset_bucket = (
            "early" if onset is not None and onset <= 10
            else "mid" if onset is not None and onset <= 40
            else "late"
        )

        races[rid] = {
            "race_id": rid,
            "track_id": stints[0]["track_id"],
            "track_name": stints[0]["track_name"],
            "season_year": stints[0]["season_year"],
            "round_number": stints[0]["round_number"],
            "race_date": stints[0]["race_date"],
            "regulation_era": stints[0]["regulation_era"],
            "rain_onset_lap": onset,
            "onset_bucket": onset_bucket,
            "before_rain": before,
            "first_wet": str(first_wet["compound"]).upper(),
            "after_wet": after,
        }

    return races


def classify_target(onset: int | None) -> str:
    if onset is None:
        return "EARLY_WET"
    if onset <= 10:
        return "EARLY_WET"
    if onset <= 40:
        return "MID_TRANSITION"
    return "LATE_WET"


def predict(
    track_id: int,
    race_date: date,
    *,
    rain_onset_lap: int | None,
    regulation_era: str,
):
    target_regime = classify_target(rain_onset_lap)

    previous_era = {
        "era3_2026regs": "era2_18inch_groundeffect",
        "era2_18inch_groundeffect": "era1_13inch",
        "era1_13inch": None,
    }.get(regulation_era)

    with ENGINE.connect() as conn:
        same_era = load_targets(
            conn,
            race_date,
            regulation_era,
        )

        candidates = [
            x for x in same_era.values()
            if classify_target(x["rain_onset_lap"]) == target_regime
        ]

        scope = "same-era, same-regime"
        penalty = 0

        # Regime-specific fallback.
        if not candidates:
            candidates = list(same_era.values())
            scope = "same-era, all-wet-transition"
            penalty = 10

        # Previous era fallback.
        if not candidates and previous_era:
            previous = load_targets(
                conn,
                race_date,
                previous_era,
            )

            candidates = [
                x for x in previous.values()
                if classify_target(x["rain_onset_lap"]) == target_regime
            ]

            if candidates:
                scope = "prior-era, same-regime"
                penalty = 20
            else:
                candidates = list(previous.values())
                scope = "prior-era, all-wet-transition"
                penalty = 25

    if not candidates:
        return {
            "status": "insufficient_history",
            "model": "wet_transition_v1",
            "regime": target_regime,
            "evidence_scope": scope,
            "confidence": 0,
            "prediction": None,
        }

    # Score each historical race.
    scored = []

    for race in candidates:
        score = 0.0

        if race["track_id"] == track_id:
            score += 5

        if race["onset_bucket"] == (
            "early" if rain_onset_lap is not None and rain_onset_lap <= 10
            else "mid" if rain_onset_lap is not None and rain_onset_lap <= 40
            else "late"
        ):
            score += 4

        if (
            rain_onset_lap is not None
            and race["rain_onset_lap"] is not None
        ):
            delta = abs(rain_onset_lap - race["rain_onset_lap"])
            score += max(0, 3 - delta / 15)

        age = max(
            0,
            (race_date - race["race_date"]).days / 365.25,
        )
        score += max(0, 2 - 0.4 * age)

        scored.append((score, race))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Build independent distributions.
    distributions = {}

    for field in (
        "before_rain",
        "first_wet",
        "after_wet",
    ):
        counter = Counter()

        for score, race in scored:
            value = race[field]

            if value is not None:
                counter[value] += score

        distributions[field] = counter

    prediction = {}

    shares = []

    for field, counter in distributions.items():
        if not counter:
            prediction[field] = None
            continue

        value, value_score = counter.most_common(1)[0]
        total = sum(counter.values())

        prediction[field] = {
            "compound": value,
            "evidence_share": round(value_score / total, 4),
            "supporting_races": sum(
                1
                for _, r in scored
                if r[field] == value
            ),
        }

        shares.append(value_score / total)

    base_confidence = (
        sum(shares) / len(shares)
        if shares
        else 0
    )

    confidence = int(round(base_confidence * 100))
    confidence -= penalty

    # Small evidence bonus.
    if len(candidates) >= 5:
        confidence += 5
    elif len(candidates) >= 3:
        confidence += 3

    confidence = max(20, min(75, confidence))

    supporting_races = [
        {
            "race_id": r["race_id"],
            "season_year": r["season_year"],
            "round_number": r["round_number"],
            "track_name": r["track_name"],
            "rain_onset_lap": r["rain_onset_lap"],
            "before_rain": r["before_rain"],
            "first_wet": r["first_wet"],
            "after_wet": r["after_wet"],
        }
        for _, r in scored[:8]
    ]

    return {
        "status": "ok",
        "model": "wet_transition_v1",
        "regulation_era": regulation_era,
        "regime": target_regime,
        "rain_onset_lap": rain_onset_lap,
        "evidence_scope": scope,
        "confidence": confidence,
        "prediction": prediction,
        "nearest_evidence": supporting_races,
    }


def validate(race_id: int):
    with ENGINE.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    r.id AS race_id,
                    r.track_id,
                    r.race_date,
                    r.regulation_era,
                    sw.rain_onset_lap,
                    rs.stint_number,
                    rs.compound,
                    rs.start_lap,
                    rs.end_lap
                FROM races r
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
                WHERE r.id = :race_id
                ORDER BY rs.stint_number
            """),
            {"race_id": race_id},
        ).mappings().all()

    if not rows:
        raise ValueError(f"Race {race_id} not found")

    first = rows[0]
    compounds = [str(x["compound"]).upper() for x in rows]

    wet_idxs = [
        i for i, c in enumerate(compounds)
        if c in WET
    ]

    first_wet_i = wet_idxs[0]
    last_wet_i = wet_idxs[-1]

    actual_before = (
        compounds[first_wet_i - 1]
        if first_wet_i > 0
        else compounds[first_wet_i]
    )

    actual_first_wet = compounds[first_wet_i]

    actual_after = None
    for c in compounds[last_wet_i + 1:]:
        if c not in WET:
            actual_after = c
            break

    result = predict(
        first["track_id"],
        first["race_date"],
        rain_onset_lap=first["rain_onset_lap"],
        regulation_era=first["regulation_era"],
    )

    p = result.get("prediction") or {}

    predicted = (
        p.get("before_rain", {}).get("compound")
        if p.get("before_rain")
        else None,
        p.get("first_wet", {}).get("compound")
        if p.get("first_wet")
        else None,
        p.get("after_wet", {}).get("compound")
        if p.get("after_wet")
        else None,
    )

    actual = (
        actual_before,
        actual_first_wet,
        actual_after,
    )

    return {
        "race_id": race_id,
        "regime": result["regime"],
        "scope": result["evidence_scope"],
        "confidence": result["confidence"],
        "actual": actual,
        "predicted": predicted,
        "field_matches": {
            "before_rain": actual[0] == predicted[0],
            "first_wet": actual[1] == predicted[1],
            "after_wet": actual[2] == predicted[2],
        },
    }


if __name__ == "__main__":
    print("================================")
    print("WET TRANSITION V1")
    print("================================")

    for race_id in [137, 150, 134, 109, 88]:
        try:
            x = validate(race_id)
            print(
                race_id,
                "regime=", x["regime"],
                "scope=", x["scope"],
                "confidence=", x["confidence"],
                "actual=", x["actual"],
                "predicted=", x["predicted"],
                "matches=", x["field_matches"],
            )
        except Exception as e:
            print(race_id, "ERROR:", repr(e))
