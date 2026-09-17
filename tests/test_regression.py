from datetime import date

import pytest

from race_strategy_calibration_v1 import (
    BetaPosterior,
    _beta_binomial_interval,
    _beta_posterior,
    _estimate_hazard,
)


def test_beta_posterior_mean():
    posterior = _beta_posterior(3, 7)
    assert posterior.alpha == pytest.approx(4.0)
    assert posterior.beta == pytest.approx(8.0)
    assert posterior.mean == pytest.approx(1 / 3)


def test_beta_binomial_interval_is_ordered():
    lo, hi = _beta_binomial_interval(3, 7)
    assert 0.0 <= lo <= hi <= 1.0


def test_strategy_production_weather_failure(monkeypatch):
    from datetime import date as dt_date

    import fetch_race_forecast as weather
    import strategy_production_v6 as v6

    def fail_forecast(*args, **kwargs):
        raise RuntimeError("TEST: simulated Open-Meteo outage")

    monkeypatch.setattr(weather, "fetch_forecast", fail_forecast)
    monkeypatch.setattr(
        v6,
        "predict_race_conditions",
        weather.predict_race_conditions,
    )

    class FakeDate:
        @classmethod
        def today(cls):
            # Keep this regression test on the near-term forecast path even
            # after the hard-coded 2026 target race becomes historical.
            return dt_date(2026, 9, 1)

    monkeypatch.setattr(weather, "date", FakeDate)

    result = v6.predict(36, date(2026, 9, 13))

    assert result["status"] == "error"
    assert result["weather_status"] == "unavailable"
    assert result["tier"] == "forecast"
    assert result["wet_strategy_trigger"] is False
    assert result["rain_expected"] is None
    assert "Weather unavailable:" in result["message"]


def test_strategy_production_weather_success_shape(monkeypatch):
    import fetch_race_forecast as weather
    import strategy_production_v6 as v6

    def fake_forecast(*args, **kwargs):
        return {
            "time": ["2026-09-13T12:00"],
            "temperature_2m": [25.0],
            "precipitation_probability": [0],
        }

    monkeypatch.setattr(weather, "fetch_forecast", fake_forecast)
    monkeypatch.setattr(
        v6,
        "predict_race_conditions",
        weather.predict_race_conditions,
    )

    result = v6.predict(36, date(2026, 9, 13))

    assert result["status"] == "ok"
    assert result["tier"] == "forecast"
    assert "wet_recommendation" in result
