from __future__ import annotations


def classify(
    *,
    rain_expected,
    rain_onset_lap,
    forecast_tier=None,
):
    """
    Conservative wet-regime classifier.

    Historical validation showed:
      - INTERMEDIATE is the 10/11 genuine-wet baseline.
      - WET-first prediction has no demonstrated edge.
      - Wet duration cannot be predicted reliably from current data.

    Therefore this module intentionally does NOT predict exact
    wet duration or promote WET based on unsupported heuristics.
    """

    wet = bool(rain_expected)

    if not wet:
        return {
            "weather_context": "dry",
            "wet_race_warning": False,
            "first_wet_compound": None,
            "wet_duration_regime": None,
            "wet_duration_laps": None,
            "wet_model": "baseline",
            "wet_model_reliability": "not_applicable",
            "reason": "No rain expected by forecast.",
        }

    return {
        "weather_context": "wet",
        "wet_race_warning": True,

        # Empirical baseline: 10/11 genuine-wet winner races
        # in the validated 2022-2025 set were I-first.
        "first_wet_compound": "INTERMEDIATE",

        # Deliberately unknown. V2 achieved only 36.4% duration
        # bucket accuracy vs 54.5% majority baseline.
        "wet_duration_regime": None,
        "wet_duration_laps": None,

        "wet_model": "wet_regime_v1",
        "wet_model_reliability": "baseline",

        "forecast_tier": forecast_tier,
        "rain_onset_lap": rain_onset_lap,

        "reason": (
            "INTERMEDIATE baseline selected; "
            "no validated signal supports WET-first or exact "
            "wet-duration prediction."
        ),
    }


if __name__ == "__main__":
    print("WET REGIME V1")
    print("=============")

    tests = [
        {
            "rain_expected": False,
            "rain_onset_lap": None,
            "forecast_tier": "forecast",
        },
        {
            "rain_expected": True,
            "rain_onset_lap": 1,
            "forecast_tier": "forecast",
        },
        {
            "rain_expected": True,
            "rain_onset_lap": 30,
            "forecast_tier": "forecast",
        },
    ]

    for t in tests:
        print(classify(**t))
