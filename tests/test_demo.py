"""The demo: made-up data at three fixed moments, no outside calls. The moments are also the project's fixtures:
each one's summary is pinned here, so a change to the rules or the page that changes the story shows up.

The server reads its config at import, so these tests reload it with DEMO=1 and reload it again afterwards
(the reference config, from conftest) for the other test files."""
import importlib
import json
import math
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import server as server_module

MOMENTS = ("rush", "clear", "humid")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def demo():
    os.environ["DEMO"] = "1"
    try:
        srv = importlib.reload(server_module)
        srv.app.config["TESTING"] = True
        yield srv
    finally:
        os.environ.pop("DEMO", None)
        importlib.reload(server_module)


@pytest.fixture
def c(demo):
    with demo.app.test_client() as client:
        yield client


def pick(m, *keys):
    return next((m[k] for k in keys if m.get(k) is not None), None)


def payload(current):
    """What the page posts to /api/analyze, built from /api/current the way app.js's readPayload does (a home without
    one of the monitors posts nothing for it)."""
    i = next((m for m in current if m["locationType"] == "indoor"), {})
    o = next((m for m in current if m["locationType"] == "outdoor"), {})
    return {"format": "read",
            "indoor_pm25": pick(i, "pm02_corrected", "pm02"), "indoor_co2": pick(i, "rco2_corrected", "rco2"),
            "indoor_humidity": pick(i, "rhum", "rhum_corrected"), "indoor_temp_c": pick(i, "atmp", "atmp_corrected"),
            "outdoor_pm25": pick(o, "pm02_corrected", "pm02"), "outdoor_humidity": pick(o, "rhum_corrected", "rhum"),
            "outdoor_temp_c": pick(o, "atmp_corrected", "atmp"),
            "indoor_location_id": i.get("locationId"), "outdoor_location_id": o.get("locationId")}


def read(c, moment):
    current = c.get(f"/api/current?moment={moment}").get_json()
    return c.post(f"/api/analyze?moment={moment}", json=payload(current)).get_json()


def week_url(moment, location_id):
    """The page's 7-day history request, ending at the moment's time."""
    from demo.moments import moment_for
    to = moment_for(moment).at.astimezone(timezone.utc)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return f"/api/history/{location_id}?from={iso(to - timedelta(hours=168))}&to={iso(to)}&moment={moment}"


def profiles_of(summary):
    return {n["id"]: n for n in summary["profiles"]}


def test_no_outside_calls(demo, c, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the demo called the network")
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    for m in MOMENTS:
        assert c.get(f"/?moment={m}").status_code == 200
        current = c.get(f"/api/current?moment={m}")
        assert current.status_code == 200
        assert c.get(f"/api/context?moment={m}").status_code == 200
        nearby = c.get(f"/api/neighborhood?moment={m}")
        assert nearby.status_code == 200 and len(nearby.get_json()) >= 8
        for mon in current.get_json():
            hist = c.get(week_url(m, mon["locationId"]))
            assert hist.status_code == 200 and len(hist.get_json()) > 1000   # a week of 5-minute readings
        r = c.post(f"/api/analyze?moment={m}", json=payload(current.get_json()))
        assert r.status_code == 200 and r.get_json()["summary"] is not None


def test_rush_story(c):
    r = read(c, "rush")
    s = r["summary"]
    items = {i["id"]: i for i in s["items"]}
    # the first sample people see stays simple: PM2.5 (the BQE's exhaust) leads, CO₂ follows, nothing else; pollen is
    # low and the bedroom's humidity comfortable, so no pollen action, no humidity line and only exhaust in the sky
    assert [i["id"] for i in s["items"]] == ["pm", "co2"]
    assert s["also"] is None
    app = app_of(c, "rush")
    assert payload(c.get("/api/current?moment=rush").get_json())["indoor_humidity"] <= app["limits"]["rh_good"]
    # the tension: stale inside, dirty outside, so air out after the rush
    co2 = items["co2"]
    assert co2["action"]["text"] == "Open a window for 10 min"
    assert co2["action"]["when"] == "after 11 AM"
    assert datetime.fromisoformat(co2["action"]["when_end"]).hour >= 10
    assert co2["facts"]["since"] == "yesterday 9 PM"
    # outdoor PM2.5 rises with the BQE rush, peaks under the 35 line, and is clean again from 11
    pm = items["pm"]
    assert pm["forecast"] is True and pm["facts"]["cause"] == "BQE rush" and pm["facts"]["pm_now"] == 22
    assert 30 <= int(pm["num"]) <= 35
    assert pm["action"]["when"] == "until 11 AM"
    profiles = profiles_of(s)
    # the runner: run after the dirty stretch ends, at the same 11 AM the windows wait for
    assert profiles["runner"]["ok"] is False and profiles["runner"]["state"] == "Run after 11 AM"
    # the toddler: stale now, after a stale night that peaked about 1,400
    assert profiles["toddler"]["ok"] is False and profiles["toddler"]["state"] == "Stale indoors"
    assert 1350 <= profiles["toddler"]["facts"]["night_peak"] <= 1450 and "last night" in profiles["toddler"]["why"].lower()
    assert profiles["sinus"]["ok"] is True and profiles["sinus"]["state"] == "Pollen low"
    ctx = c.get("/api/context?moment=rush").get_json()
    assert ctx["road"] == {"name": "BQE", "toward": True}
    assert all(p["index"] < app["limits"]["pollen"] for p in ctx["pollen"])
    assert ctx["status"] == {"pollen": "ok", "smoke": "ok", "road": "ok", "nearby": "ok"}


def test_rush_nearby_higher_near_the_road(c):
    route = json.loads((ROOT / "static" / "bqe.json").read_text())

    def to_road(n):
        return min(math.hypot((x - n["longitude"]) * 111.32 * math.cos(math.radians(n["latitude"])), (y - n["latitude"]) * 111.32) for x, y in route)
    own = {m["locationId"] for m in c.get("/api/current?moment=rush").get_json()}
    nb = [n for n in c.get("/api/neighborhood?moment=rush").get_json() if n["locationId"] not in own]
    near = [n["pm02"] for n in nb if to_road(n) < 0.6]
    far = [n["pm02"] for n in nb if to_road(n) > 1.5]
    assert near and far and min(near) > max(far)


def test_clear_story(c):
    r = read(c, "clear")
    s = r["summary"]
    assert s["items"] == [] and s["all_clear"] is True
    assert all(n["ok"] is True for n in s["profiles"]), s["profiles"]
    assert all(n["tone"] == "good" for n in r["profiles"]), r["profiles"]
    assert c.get("/api/context?moment=clear").get_json()["road"]["toward"] is False


def test_humid_story(c):
    r = read(c, "humid")
    s = r["summary"]
    assert [i["id"] for i in s["items"]] == ["rh"]
    rh = s["items"][0]
    assert rh["num"] == "66"
    assert rh["action"]["text"] == "Run the dehumidifier"   # the rules' action; its explanation names the AC's dry mode
    assert "AC’s dry mode" in rh["why"] and "Outside is wetter (92%)" in rh["why"]
    profiles = profiles_of(s)
    assert profiles["sinus"]["ok"] is False and profiles["sinus"]["state"] == "Too damp"
    assert profiles["toddler"]["ok"] is True and profiles["runner"]["ok"] is True
    ctx = c.get("/api/context?moment=humid").get_json()
    assert ctx["weather"]["weather_code"] >= 61   # rain


def test_moment_urls_differ_and_cache(c):
    rush = c.get("/api/context?moment=rush")
    clear = c.get("/api/context?moment=clear")
    assert rush.get_json() != clear.get_json()
    for resp in (rush, clear, c.get("/?moment=clear"), c.get("/api/current?moment=humid")):
        assert resp.headers["Cache-Control"] == "public, max-age=300, s-maxage=86400"
    nope = c.get("/api/context?moment=nope")
    assert nope.status_code == 200 and nope.get_json() == rush.get_json()
    assert c.get("/api/context").get_json() == rush.get_json()   # no moment: the default


def test_page_carries_the_clock(c):
    html = c.get("/?moment=humid").get_data(as_text=True)
    app = json.loads(html.split("window.APP = ", 1)[1].split(";</script>", 1)[0])
    assert app["now"]["moment"] == "humid"
    assert app["now"]["at"].startswith("2026-10-22T20:30:00")
    assert [m["key"] for m in app["now"]["moments"]] == ["rush", "clear", "humid"]
    assert [m["label"] for m in app["now"]["moments"]] == ["Morning rush", "All clear", "Humid evening"]
    assert app["place"]["name"] == "Williamsburg, Brooklyn NY"
    assert app["road"]["toward"] == [66, 207]
    assert "X-Robots-Tag" not in c.get("/").headers


def test_sample_numbers():
    from demo.moments import moment_for
    assert moment_for(None, "2").key == "clear"
    assert moment_for("humid", None).key == "humid"
    assert moment_for(None, "9").key == "rush"
    assert moment_for("humid", "1").key == "rush"   # a sample number wins over a moment key


def test_page_clock_numbers(demo):
    with demo.app.test_request_context("/"):
        assert [m["n"] for m in demo.page_clock()["moments"]] == [1, 2, 3]


def test_sample_and_moment_urls_match(c):
    for n, key in enumerate(MOMENTS, 1):
        assert c.get(f"/?sample={n}").get_data() == c.get(f"/?moment={key}").get_data()
        assert c.get(f"/api/current?sample={n}").get_json() == c.get(f"/api/current?moment={key}").get_json()


def test_deterministic(c):
    for url in ("/api/current?moment=rush", "/api/context?moment=clear", "/api/neighborhood?moment=humid"):
        assert c.get(url).get_data() == c.get(url).get_data()
    assert json.dumps(read(c, "rush"), sort_keys=True) == json.dumps(read(c, "rush"), sort_keys=True)


def test_mode(demo):
    assert demo.MODE == "demo"
    cfg = demo.CFG
    assert demo.server_mode(cfg, "") == "demo"
    reference = type(cfg)(**{**cfg.__dict__, "demo": False})
    assert demo.server_mode(reference, "tok") == "live"
    assert demo.server_mode(reference, "") == "no_token"


def test_fresh_copy_opens_as_the_demo(demo, tmp_path):
    """No config and no token: the demo. A config without a token stays that config (Task 10 says what's missing)."""
    shutil.copytree(ROOT / "demo", tmp_path / "demo")
    (tmp_path / "static").mkdir()
    shutil.copy(ROOT / "static" / "bqe.json", tmp_path / "static" / "bqe.json")
    cfg = demo.startup_config({}, tmp_path)
    assert cfg.demo is True and cfg.place.name == "Williamsburg, Brooklyn NY"
    assert demo.startup_config({"AIRGRADIENT_TOKEN": "x"}, tmp_path) is None
    (tmp_path / "config.toml").write_text((ROOT / "config.example.toml").read_text())
    assert demo.startup_config({}, tmp_path).demo is False


# ── the hidden one-monitor cases (?moment=indoor-near, indoor-model, outdoor-only): not samples, not in the top area ──

SKY_MOMENTS = ("sky-smoke", "sky-pollen", "sky-smoke-damp", "sky-particles")
HIDDEN = ("indoor-near", "indoor-model", "outdoor-only") + SKY_MOMENTS
SOURCE_ID = 900001   # the made-up public monitor indoor-near reads


def app_of(c, moment):
    html = c.get(f"/?moment={moment}").get_data(as_text=True)
    return json.loads(html.split("window.APP = ", 1)[1].split(";</script>", 1)[0])


def test_hidden_moments_load_without_outside_calls(c, monkeypatch):
    """Each hidden case's page, its window.APP and every request the page makes answer, with no network."""
    from demo.moments import moment_for
    def refuse(*a, **k):
        raise AssertionError("the demo called the network")
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    for m in HIDDEN:
        app = app_of(c, m)
        assert app["now"]["moment"] == m and app["now"]["at"] == moment_for(m).at.isoformat()
        assert [x["key"] for x in app["now"]["moments"]] == ["rush", "clear", "humid"]   # the top area keeps the three
        current = c.get(f"/api/current?moment={m}")
        assert current.status_code == 200
        assert c.get(f"/api/context?moment={m}").status_code == 200
        assert c.get(f"/api/neighborhood?moment={m}").status_code == 200
        for mon in current.get_json():
            if "locationId" in mon:   # an estimate has no history to ask for
                hist = c.get(week_url(m, mon["locationId"]))
                assert hist.status_code == 200 and len(hist.get_json()) > 1000
        r = c.post(f"/api/analyze?moment={m}", json=payload(current.get_json()))
        assert r.status_code == 200 and r.get_json()["summary"] is not None


def test_hidden_moments_stay_hidden(c):
    from demo.moments import SAMPLES, moment_for
    assert SAMPLES == ("rush", "clear", "humid")
    for m in HIDDEN:
        assert moment_for(m).key == m
    assert all(m not in SAMPLES for m in HIDDEN)
    assert c.get("/?moment=indoor-near").get_data() != c.get("/?moment=rush").get_data()


def test_sky_smoke_story(c):
    current = payload(c.get("/api/current?moment=sky-smoke").get_json())
    assert current["outdoor_pm25"] == 48 and current["indoor_humidity"] == 45
    s = read(c, "sky-smoke")["summary"]
    assert [i["id"] for i in s["items"]] == ["pm"]
    assert s["items"][0]["action"] and s["items"][0]["facts"]["cause"] == "smoke"
    ctx = c.get("/api/context?moment=sky-smoke").get_json()
    assert ctx["smoke"]["detected"] is True and ctx["road"]["toward"] is False
    assert ctx["smoke"]["density"] == "Medium"
    assert all(p["index"] == 0 for p in ctx["pollen"])


def test_sky_pollen_story(c):
    current = payload(c.get("/api/current?moment=sky-pollen").get_json())
    assert current["outdoor_pm25"] == 5 and current["indoor_humidity"] == 45
    s = read(c, "sky-pollen")["summary"]
    assert [i["id"] for i in s["items"]] == ["pollen"] and s["all_clear"] is False
    assert s["items"][0]["action"] and s["items"][0]["num"] == "4"
    ctx = c.get("/api/context?moment=sky-pollen").get_json()
    assert ctx["smoke"]["detected"] is False and ctx["road"]["toward"] is False


def test_sky_smoke_damp_story(c):
    app = app_of(c, "sky-smoke-damp")
    current = payload(c.get("/api/current?moment=sky-smoke-damp").get_json())
    assert current["outdoor_pm25"] == 48 > app["limits"]["pm_high"]
    assert current["indoor_humidity"] == 66 > app["limits"]["rh_damp"]
    s = read(c, "sky-smoke-damp")["summary"]
    assert [i["id"] for i in s["items"]] == ["pm", "rh"]
    assert s["items"][0]["action"] and s["items"][0]["facts"]["cause"] == "smoke"
    assert s["items"][1]["action"]["text"] == "Run the dehumidifier"
    ctx = c.get("/api/context?moment=sky-smoke-damp").get_json()
    assert ctx["smoke"]["detected"] is True and ctx["road"]["toward"] is False
    assert all(p["index"] == 0 for p in ctx["pollen"])


def test_sky_particles_story(c):
    """Fine particles: outdoor PM2.5 over the limit (not the high limit), no smoke on the map, the road's exhaust blown
    away, nothing else over its limit, and the PM2.5 action leading. The page's rule (static/home.js) names it."""
    app = app_of(c, "sky-particles")
    current = payload(c.get("/api/current?moment=sky-particles").get_json())
    assert app["limits"]["pm"] < current["outdoor_pm25"] == 24 <= app["limits"]["pm_high"]
    assert current["indoor_humidity"] <= app["limits"]["rh_damp"]
    s = read(c, "sky-particles")["summary"]
    assert [i["id"] for i in s["items"]] == ["pm"] and s["all_clear"] is False
    assert s["items"][0]["action"]["text"] and "cause" not in s["items"][0]["facts"]
    ctx = c.get("/api/context?moment=sky-particles").get_json()
    assert ctx["smoke"]["detected"] is False and ctx["road"]["toward"] is False
    assert all(p["index"] < app["limits"]["pollen"] for p in ctx["pollen"])


@pytest.mark.parametrize("moment", ("sky-smoke", "sky-smoke-damp"))
def test_smoke_windows_reopen_at_first_clean_forecast_hour(c, moment):
    from demo.moments import moment_for
    from summary import OUTLOOK_HOURS

    now = moment_for(moment).at
    summary = read(c, moment)["summary"]
    forecast = c.get(f"/api/context?moment={moment}").get_json()["forecast"]
    limit = app_of(c, moment)["limits"]["pm"]
    clean = [datetime.fromisoformat(h["time"]).replace(tzinfo=now.tzinfo) for h in forecast
             if h["pm25"] is not None and round(h["pm25"]) <= limit
             and now < datetime.fromisoformat(h["time"]).replace(tzinfo=now.tzinfo)
             <= now + timedelta(hours=OUTLOOK_HOURS)]
    assert clean, "smoke must clear within the Summary's forecast window"
    assert datetime.fromisoformat(summary["items"][0]["action"]["when_end"]) == clean[0]


@pytest.mark.parametrize("moment", ("sky-smoke", "sky-smoke-damp"))
def test_smoke_is_regional_in_nearby_readings(c, moment):
    current = c.get(f"/api/current?moment={moment}").get_json()
    home = next(m for m in current if m["locationType"] == "outdoor")
    nearby = c.get(f"/api/neighborhood?moment={moment}").get_json()
    pm = [m["pm02"] for m in nearby if m["locationId"] != home["locationId"]]
    assert len(pm) == 12
    assert pm == pytest.approx([home["pm02_corrected"]] * len(pm), abs=3)
    assert min(pm) < max(pm)


@pytest.mark.parametrize("moment", SKY_MOMENTS)
def test_sky_history_is_a_full_week_ending_at_current_reading(c, moment):
    from demo.moments import moment_for

    at = moment_for(moment).at
    step = timedelta(minutes=5)
    for monitor in c.get(f"/api/current?moment={moment}").get_json():
        rows = c.get(week_url(moment, monitor["locationId"])).get_json()
        times = [datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) for row in rows]
        assert len(times) == timedelta(days=7) // step + 1
        assert times[-1] == at
        assert all(b - a == step for a, b in zip(times, times[1:]))
        assert rows[-1] == {field: monitor[field] for field in rows[-1]}


@pytest.mark.parametrize("moment", ("sky-smoke", "sky-smoke-damp"))
def test_smoke_weather_is_partly_cloudy(c, moment):
    weather = c.get(f"/api/context?moment={moment}").get_json()["weather"]
    assert weather["cloud_cover"] == 55
    assert weather["weather_code"] == 2


def test_indoor_near_story(c):
    """An indoor-only home that reads a public monitor 1.1 km away: the rush story, told from that monitor."""
    app = app_of(c, "indoor-near")
    assert app["place"]["indoor"] == "bedroom" and app["place"]["outdoor"] is None
    assert app["outdoor_from"] == {"kind": "monitor", "id": SOURCE_ID}
    current = c.get("/api/current?moment=indoor-near").get_json()
    assert sorted(m["locationType"] for m in current) == ["indoor", "outdoor"]
    out = next(m for m in current if m["locationType"] == "outdoor")
    assert out["locationId"] == SOURCE_ID and out["pm02_corrected"] == 22   # the rush's 22, read by the public monitor
    src = out["source"]
    assert src["kind"] == "monitor" and src["id"] == SOURCE_ID and src["distance_km"] == 1.1
    assert src["name"] == "Larkspur Yard" and src["contributor"] == "Demo Neighbor" and src["fallback"] is False
    nb = {n["locationId"] for n in c.get("/api/neighborhood?moment=indoor-near").get_json()}
    assert SOURCE_ID in nb and 4202 not in nb   # the public monitor among its neighbors; no balcony monitor

    r = read(c, "indoor-near")
    assert r["outdoor_source"] == {"kind": "monitor", "name": "Larkspur Yard", "distance_km": 1.1, "area_count": 0,
                                   "fallback": False}
    s = r["summary"]
    items = {i["id"]: i for i in s["items"]}
    assert set(items) == {"pm", "co2", "pollen"}
    pm = items["pm"]
    assert ("scaled to what the public monitor at Larkspur Yard, 1.1 km away, measured over the last day."
            in pm["why"]), pm["why"]
    # the actions as in the rush story
    assert pm["forecast"] is True and pm["facts"]["cause"] == "BQE rush" and pm["facts"]["pm_now"] == 22
    assert pm["action"]["when"] == "until 11 AM"
    assert items["co2"]["action"]["text"] == "Open a window for 10 min" and items["co2"]["action"]["when"] == "after 11 AM"
    profiles = profiles_of(s)
    assert profiles["runner"]["state"] == "Run after 11 AM"
    assert profiles["toddler"]["state"] == "Stale indoors" and profiles["sinus"]["state"] == "Pollen high"
    # the sinus sentence keeps the CO₂ airing beside its "windows shut", as the pollen card does
    assert "except a short airing for CO₂" in profiles["sinus"]["why"], profiles["sinus"]["why"]


def test_indoor_model_story(c):
    """An indoor-only home on the model alone: PM2.5 is a forecast estimate, with nothing outside to check it against."""
    app = app_of(c, "indoor-model")
    assert app["place"]["indoor"] == "bedroom" and app["place"]["outdoor"] is None
    assert app["outdoor_from"] == {"kind": "model", "id": None}
    current = c.get("/api/current?moment=indoor-model").get_json()
    out = next(m for m in current if m["locationType"] == "outdoor")
    assert "locationId" not in out and "pm02" not in out and out["source"]["kind"] == "model"
    assert 4202 not in {n["locationId"] for n in c.get("/api/neighborhood?moment=indoor-model").get_json()}

    r = read(c, "indoor-model")
    assert r["outdoor_source"]["kind"] == "model"
    s = r["summary"]
    items = {i["id"]: i for i in s["items"]}
    pm = items["pm"]
    assert pm["forecast"] is True and pm["facts"].get("pm_now") is None   # no reading now: the forecast alone
    assert ("The forecast is Open-Meteo’s prediction for the wider area: an estimate, with no monitor outside to check "
            "it against.") in pm["why"], pm["why"]
    # without a reading now, the dirty stretch is the forecast's, from 8 (in the BQE's morning rush, wind toward home):
    # air now, before it, so the airing doesn't read as a clash with "Windows shut · 8–11 AM"; the runner avoids it
    assert pm["action"]["when"] == "8–11 AM"
    co2 = items["co2"]
    assert co2["action"]["text"] == "Open a window for 10 min" and co2["action"]["when"] == "now, before the 8 AM rush"
    assert "Do it before 8 AM, when the rush fumes arrive." in co2["why"], co2["why"]
    assert profiles_of(s)["runner"]["state"] == "Avoid 8–11 AM"


def test_outdoor_only_story(c):
    """An outdoor-only home: no indoor monitor, so no CO₂, the toddler waits for one, the sinus profile on pollen only
    (the case's own high tree pollen: the rush's is low)."""
    app = app_of(c, "outdoor-only")
    assert app["place"]["indoor"] is None and app["place"]["outdoor"] == "balcony"
    assert "outdoor_from" not in app
    current = c.get("/api/current?moment=outdoor-only").get_json()
    assert [m["locationType"] for m in current] == ["outdoor"] and current[0]["locationId"] == 4202

    r = read(c, "outdoor-only")
    assert "outdoor_source" not in r
    s = r["summary"]
    items = {i["id"]: i for i in s["items"]}
    assert "co2" not in items and "rh" not in items
    assert set(items) == {"pm", "pollen"}
    assert "Humidity inside" not in (s["also"] or "")
    profiles = profiles_of(s)
    assert profiles["toddler"]["state"] == "Needs an indoor monitor"
    assert profiles["sinus"]["state"] == "Pollen high" and "pollen only" in profiles["sinus"]["why"]
    assert profiles["runner"]["state"] == "Run after 11 AM"


@pytest.mark.parametrize("moment", ["rush", "clear", "humid", "indoor-near", "indoor-model", "outdoor-only"])
def test_the_agent_gives_the_page_actions_and_misses_no_input(c, moment):
    """An assistant and the page say the same thing for the same moment: the agent builds its own request to the rules
    (agent_tools.now), so a field read differently there would change its actions silently."""
    page = [(i["label"], (i.get("action") or {}).get("text"), (i.get("action") or {}).get("when"))
            for i in read(c, moment)["summary"]["items"]]
    agent = c.get(f"/api/agent/now?moment={moment}").get_json()
    assert [(a["what"], a["action"], a["when"]) for a in agent["read"]["actions"]] == page
    assert set(agent["inputs"]) == {"indoor", "outdoor", "weather", "pollen", "smoke", "road"}
    assert not [k for k, e in agent["inputs"].items() if e["status"] == "missing"], agent["inputs"]
