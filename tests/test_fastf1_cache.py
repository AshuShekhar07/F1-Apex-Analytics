from types import SimpleNamespace

import fastf1

from fastf1_cache import forget_session


def test_forget_session_drops_the_saved_copy_of_that_session_only(tmp_path, monkeypatch):
    # FastF1's cache settings are process-wide: give them back after the test
    for attr in ("_CACHE_DIR", "_requests_session_cached", "_IGNORE_VERSION", "_FORCE_RENEW"):
        monkeypatch.setattr(fastf1.Cache, attr, getattr(fastf1.Cache, attr, None), raising=False)
    fastf1.Cache.enable_cache(str(tmp_path))
    path = "/static/2026/2026-10-11_Singapore_Grand_Prix/2026-10-10_Qualifying/"
    saved = tmp_path / path.removeprefix("/static/")
    saved.mkdir(parents=True)
    (saved / "timing_data.ff1pkl").write_bytes(b"partial")
    other = tmp_path / "2026/2026-10-11_Singapore_Grand_Prix/2026-10-09_Practice_1"
    other.mkdir(parents=True)
    assert forget_session(SimpleNamespace(api_path=path)) >= 1
    assert not saved.exists() and other.exists()
    assert forget_session(SimpleNamespace(api_path=path)) == 0            # nothing left to drop
    assert forget_session(SimpleNamespace(api_path=None)) == 0
