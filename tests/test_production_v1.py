"""Production settings, health/about endpoints, rate limiting and brand-asset scrubbing."""

import dataclasses

import pytest

from app.settings import load_settings, scrub_brand_assets


def test_settings_default_to_development_and_lock_down_in_production(monkeypatch):
    for name in ("APP_ENV", "CORS_ORIGINS", "SERVE_BRAND_ASSETS", "RATE_LIMIT_PER_MINUTE", "WARM_CACHES"):
        monkeypatch.delenv(name, raising=False)
    dev = load_settings()
    assert dev.env == "development" and "http://localhost:5173" in dev.cors_origins
    assert dev.serve_brand_assets and dev.rate_limit_per_minute == 0 and not dev.warm_caches
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://apex21.example, https://www.apex21.example")
    prod = load_settings()
    assert prod.production and prod.cors_origins == ("https://apex21.example", "https://www.apex21.example")
    assert not prod.serve_brand_assets and prod.rate_limit_per_minute == 600 and prod.warm_caches
    monkeypatch.setenv("SERVE_BRAND_ASSETS", "true")
    assert load_settings().serve_brand_assets


def test_scrub_removes_logo_and_photo_urls_everywhere():
    body = {"team": {"name": "T", "logo_url": "x"}, "drivers": [{"name": "D", "photo_url": "y", "n": 1}],
            "team_logo_url": "z"}
    assert scrub_brand_assets(body) == {"team": {"name": "T"}, "drivers": [{"name": "D", "n": 1}]}


@pytest.fixture()
def client(api_client):
    return api_client


def test_health_reports_database_and_staleness(client):
    body = client.get("/health").json()
    assert body["database"] == "ok" and body["model_version"]
    # the fixture database has no pipeline runs yet -> degraded, not down
    assert body["status"] == "degraded" and "no pipeline run in the last week" in body["problems"]


def test_about_carries_the_disclaimer(client):
    body = client.get("/about").json()
    assert "Not affiliated" in body["disclaimer"] and body["finishing_order"]["mean_abs_error_places"]


def test_rate_limit_and_brand_scrub_follow_settings(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, rate_limit_per_minute=3,
                                                              serve_brand_assets=False))
    main._hits.clear()
    codes = [client.get("/about").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert client.get("/health").status_code == 200            # never limited
    main._hits.clear()
    body = client.get("/teams").json()
    assert "logo_url" not in str(body)
    main._hits.clear()
