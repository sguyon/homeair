import server
import sources
from config import public_view
from conftest import SAMPLE_PAYLOAD


def test_weather_asks_for_sun_times_and_past_hours(monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self): pass
        def json(self): return {"current": {"temperature_2m": 1}}

    def fake_get(url, params=None, timeout=None, **kw):
        seen.update(params or {})
        return R()
    monkeypatch.setattr(sources.requests, "get", fake_get)
    sources._fetch_weather(sources.home_here())
    assert seen["daily"] == "sunrise,sunset" and seen["past_hours"] == 4 and seen["forecast_days"] == 2


def test_payload_has_summary(client):
    s = client.post("/api/analyze", json={"format": "read", **SAMPLE_PAYLOAD}).get_json()["summary"]
    assert set(s) == {"label", "clock", "items", "also", "profiles", "all_clear", "timeline"} and len(s["profiles"]) == 3
    assert [i["id"] for i in s["items"]][:2] == ["co2", "rh"]


def test_read_carries_no_unread_fields(client):
    # the page reads summary, generated_at and clock; the old card's headline, cards, checked, ahead and the pollen
    # relief line had no reader left
    r = client.post("/api/analyze", json={"format": "read", **SAMPLE_PAYLOAD}).get_json()
    assert not {"headline", "cards", "checked", "ahead", "relief", "relief_short"} & set(r)
    assert {"summary", "generated_at", "clock"} <= set(r)


def test_history_keeps_humidity_for_the_chart(monkeypatch, client):
    import server
    row = {"timestamp": "2026-11-08T10:00:00.000Z", "pm02": 3, "rco2": 600, "rhum": 52, "rhum_corrected": 51, "atmp": 21}
    monkeypatch.setattr(sources, "api_get", lambda path, params=None: [row])
    rows = client.get("/api/history/1?from=2026-11-01T00:00:00Z&to=2026-11-02T00:00:00Z").get_json()
    assert rows[0]["rhum_corrected"] == 51 and rows[0]["rhum"] == 52 and "atmp" not in rows[0]


def test_canned_smoke_is_off_like_the_real_function(client):
    s = client.post("/api/analyze", json={"format": "read", **SAMPLE_PAYLOAD}).get_json()["summary"]
    assert next(n for n in s["profiles"] if n["id"] == "runner")["facts"]["smoke"] is False


def test_analyze_profiles_read_format(client):
    for body in ({}, {"format": "stream"}):
        r = client.post("/api/analyze", json=body)
        assert r.status_code == 400 and "error" in r.get_json()


def test_card_is_redrawn_on_every_read(client):
    a = client.post("/api/analyze", json={"format": "read", **SAMPLE_PAYLOAD, "indoor_co2": 1310}).get_json()
    b = client.post("/api/analyze", json={"format": "read", **SAMPLE_PAYLOAD, "indoor_co2": 1320}).get_json()
    num = lambda r: next(i for i in r["summary"]["items"] if i["id"] == "co2")["num"]
    assert num(a) == "1,310" and num(b) == "1,320"


def test_home_without_outdoor_history_draws_no_model_past(monkeypatch):
    """The model's past hours stand in for a public monitor's history only: a home whose outdoor history is missing
    (AirGradient failed, or no outdoor monitor) draws no past PM2.5, as before."""
    from fixtures import golden_inputs as gi
    gi.install(server, monkeypatch, gi.EVENING)
    monkeypatch.setattr(sources, "history_points", lambda lid, now, hours=24: gi.history(lid, now, hours) if lid == 1 else [])
    with server.app.test_client() as c:
        pm = c.post("/api/analyze", json={"format": "read", **gi.PAYLOAD}).get_json()["summary"]["timeline"]["pm"]
    assert "source" not in pm and pm["past"] == [] and pm["fc"]


def test_outdoor_only_home_reads_nothing_indoors(client, monkeypatch):
    """An outdoor-only home: even if the page posts indoor readings, the summary has no CO₂ or humidity item, the
    toddler waits for an indoor monitor and the sinus profile runs on pollen alone."""
    from config import load_config
    from conftest import REFERENCE_TOML
    before = server.CFG
    server.use_config(load_config({"CONFIG_TOML": REFERENCE_TOML.replace('indoor = "apartment"\n', "")}))
    try:
        monkeypatch.setattr(sources, "get_pollen_days", lambda *a, **k: {"today": [{"name": "Tree", "index": 1}], "tomorrow": []})
        monkeypatch.setenv("GOOGLE_POLLEN_API_KEY", "test-key")
        s = client.post("/api/analyze", json={"format": "read", **SAMPLE_PAYLOAD}).get_json()["summary"]
    finally:
        server.use_config(before)
    assert not {"co2", "rh"} & {i["id"] for i in s["items"]}
    states = {n["id"]: (n["ok"], n["state"]) for n in s["profiles"]}
    assert states["toddler"] == (None, "Needs an indoor monitor")
    assert states["sinus"] == (True, "Pollen low")
    assert "pollen only: no indoor monitor for humidity" in next(n for n in s["profiles"] if n["id"] == "sinus")["why"]
    assert "config" not in str(s).lower()


def test_dev_server_keeps_the_days_pollen_on_disk(monkeypatch, tmp_path):
    """The reloading dev server reuses the day's pollen across restarts; production never writes it."""
    import server
    calls = []
    monkeypatch.setattr(sources, "_fetch_pollen", lambda: calls.append(1) or {"today": [{"name": "Tree"}], "tomorrow": []})
    monkeypatch.setattr(sources, "DEV_CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    sources._fetch_pollen_kept()
    assert calls == [1] and not (tmp_path / "cache").exists()   # production: nothing stored
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    first, again = sources._fetch_pollen_kept(), sources._fetch_pollen_kept()   # a restart reads the file
    assert first == again == {"today": [{"name": "Tree"}], "tomorrow": []} and calls == [1, 1]


def test_pollen_is_asked_once_a_day(monkeypatch):
    """A daily forecast: the second read the same day, hours later, is the first answer; a new day asks again."""
    import server
    from datetime import datetime, timedelta
    calls, clock = [], {"t": 1_000_000.0}
    monkeypatch.setattr(sources, "_fetch_pollen", lambda: calls.append(1) or {"today": [], "tomorrow": []})
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    monkeypatch.setattr(sources.time, "time", lambda: clock["t"])
    day = {"d": datetime(2026, 11, 10, 8, 0)}
    monkeypatch.setattr(sources, "local_now", lambda *a: day["d"])
    with sources._cache_lock:
        for k in [k for k in sources._cache if k.startswith("pollen:")]:
            del sources._cache[k]
    sources.get_pollen_days()
    clock["t"] += 14 * 3600; day["d"] += timedelta(hours=14)   # 10 PM, same day
    sources.get_pollen_days()
    assert calls == [1]
    clock["t"] += 3 * 3600; day["d"] += timedelta(hours=3)     # 1 AM, the next day
    sources.get_pollen_days()
    assert calls == [1, 1]


def test_context_roads_sorted_for_the_sky(client, tmp_path, monkeypatch):   # Done when 16, 12
    from test_summary import AUTO_BQE, auto_config, tue, weather_at
    queens = {**AUTO_BQE, "name": "Queens Boulevard", "kind": "main road", "key": "queens", "band": "within 200 m",
              "side": "NE", "toward": [316, 134], "toward_calm": [315, 135], "slow": []}
    cfg = auto_config(tmp_path, queens, AUTO_BQE)   # selection order: Queens Boulevard, then the BQE
    monkeypatch.setattr(sources, "local_now", lambda *a, **k: tue(13))
    monkeypatch.setattr(sources, "get_open_meteo_data", lambda *a, **k: weather_at(tue(13), (120, 15)))
    before = server.CFG
    server.use_config(cfg)
    try:
        rows = client.get("/api/context").get_json()["roads"]
        assert [r["strength"] for r in rows if r["reaching"]] == ["peak", "high"]   # whatever the selection order
        assert [r["name"] for r in rows] == ["the BQE", "Queens Boulevard"]
        assert set(rows[0]) == {"name", "short", "side", "reaching", "strength", "why", "until"}
        assert rows[0]["short"] == "BQE" and rows[0]["side"] == "E" and rows[0]["why"] == "slow"
        assert all(r["until"] is None or r["until"].startswith("2026-12-01T") for r in rows)
        assert [r["name"] for r in server.APP_VIEW["roads"]] == ["Queens Boulevard", "the BQE"]   # APP keeps the selection order
        assert server.APP_VIEW["roads_status"] == "ok" and all(r["on"] for r in server.APP_VIEW["roads"])
        assert not {"toward", "toward_calm", "route"} & set(server.APP_VIEW["roads"][0])   # no arc for an auto road
        monkeypatch.setattr(sources, "get_open_meteo_data", lambda *a, **k: {"current": {}})   # no weather: none reaches
        rows = client.get("/api/context").get_json()["roads"]
        assert [r["reaching"] for r in rows] == [False, False] and all(r["until"] is None for r in rows)
    finally:
        server.use_config(before)


def test_traffic_action_has_no_none_value_for_the_agent():
    import agent_tools
    item = {"label": "Traffic", "num": None, "unit": None, "ctx": "the BQE",
            "facts": {"roads": [{"name": "the BQE", "band": "within 500 m", "side": "E"}]}, "action": {"text": "x"}}
    assert agent_tools._value(item) == {"value": "within 500 m, E"}
    assert agent_tools._value({"num": "1,310", "unit": "ppm"}) == {"value": "1,310 ppm"}
    assert agent_tools._value({**item, "facts": {"roads": [{"name": "r", "band": None, "side": None}]}}) == {}


def test_app_roads_keep_every_index_with_an_off_road_between(tmp_path):   # review round 1, finding 1
    from test_summary import AUTO_BQE, auto_config
    mid = {**AUTO_BQE, "name": "Kent Avenue", "key": "kent", "on": False}
    cfg = auto_config(tmp_path, AUTO_BQE, mid, {**AUTO_BQE, "name": "Queens Boulevard", "key": "queens"})
    rows = public_view(cfg)["roads"]
    assert [(r["name"], r["on"]) for r in rows] == [("the BQE", True), ("Kent Avenue", False), ("Queens Boulevard", True)]
    assert [r["name"] for r in rows] == [r.name for r in cfg.roads]   # row i is /api/road/route?i=i


def test_agent_keeps_the_road_facts_on_any_action():
    import agent_tools
    item = {"facts": {"roads": [{"name": "the BQE", "band": "within 500 m", "side": "E", "arc": [74, 162], "lat": 40.7}]}}
    assert agent_tools._roads(item) == {"roads": [{"name": "the BQE", "band": "within 500 m", "side": "E", "reaching": True}]}
    assert agent_tools._roads({"facts": {}}) == {}
