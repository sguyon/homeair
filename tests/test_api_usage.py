"""How often the app asks AirGradient and the other outside APIs (docs/api-usage.md): one shared
fetch per refresh interval whatever the number of tabs, history extended with only the new readings, and a daily
budget per source."""
import logging
from datetime import datetime, timedelta, timezone

import pytest
import requests

import budgets
import server
import sources
import upstream


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self.payload


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def live(monkeypatch, canned):
    monkeypatch.setattr(server, "TOKEN", "T0K3N")
    monkeypatch.setattr(server, "MODE", "live")
    monkeypatch.setattr(sources, "outdoor_source", lambda *a, **k: None)
    for k in [k for k in sources._cache if k.startswith("ag:")]:
        sources._cache.pop(k, None)
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c
    for k in [k for k in sources._cache if k.startswith("ag:")]:
        sources._cache.pop(k, None)


@pytest.fixture
def airgradient(monkeypatch):
    """A stand-in AirGradient: records every call; history answers 5-minute rows between from and to."""
    calls = []

    def get(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        if url.endswith("/measures/past"):
            frm = datetime.strptime(params["from"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            to = datetime.strptime(params["to"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            t, rows = frm.replace(second=0, microsecond=0, minute=frm.minute - frm.minute % 5), []
            while t <= to:
                if t >= frm:
                    rows.append({"timestamp": stamp(t), "pm02": 3, "rco2": 600})
                t += timedelta(minutes=5)
            return Resp(rows)
        return Resp([{"locationId": 1, "locationName": "Living room", "locationType": "indoor", "rco2": 600,
                      "timestamp": stamp(datetime.now(timezone.utc))}])
    monkeypatch.setattr(sources.requests, "get", get)
    return calls


def age(key, seconds):
    at, value = sources._cache[key]
    sources._cache[key] = (at - seconds, value)


def test_refresh_interval_defaults_to_five_minutes_with_a_one_minute_floor():
    assert sources.refresh_seconds({}) == 300
    assert sources.refresh_seconds({"AIRGRADIENT_REFRESH_SECONDS": "900"}) == 900
    assert sources.refresh_seconds({"AIRGRADIENT_REFRESH_SECONDS": "5"}) == sources.REFRESH_FLOOR_SECONDS == 60
    assert sources.refresh_seconds({"AIRGRADIENT_REFRESH_SECONDS": "soon"}) == 300


def test_many_tabs_share_one_current_fetch_per_interval(live, airgradient):
    for _ in range(25):   # 25 tabs, or one tab reloaded 25 times
        assert live.get("/api/current").status_code == 200
    assert len(airgradient) == 1
    age("ag:current", sources.REFRESH_SECONDS - 5)
    live.get("/api/current")
    assert len(airgradient) == 1   # still inside the interval
    age("ag:current", 10)
    live.get("/api/current")
    assert len(airgradient) == 2   # one new fetch once the interval has passed


def test_refresh_button_asks_again_at_most_once_a_minute(live, airgradient):
    live.get("/api/current")
    live.get("/api/current", headers={"X-Force-Refresh": "1"})
    assert len(airgradient) == 1   # a press seconds after the last fetch is served from the cache
    age("ag:current", sources.REFRESH_FLOOR_SECONDS + 1)
    live.get("/api/current", headers={"X-Force-Refresh": "1"})
    assert len(airgradient) == 2


def hist(c, hours):
    now = datetime.now(timezone.utc)
    frm = now - timedelta(hours=hours)
    return c.get(f"/api/history/1?from={frm.strftime('%Y-%m-%dT%H:%M:%SZ')}&to={now.strftime('%Y-%m-%dT%H:%M:%SZ')}")


def test_the_ranges_up_to_a_day_share_one_fetch(live, airgradient):
    for h in (1, 6, 24, 1, 6):
        assert hist(live, h).status_code == 200
    past = [p for u, p in airgradient if u.endswith("/measures/past")]
    assert len(past) == 1
    frm = datetime.strptime(past[0]["from"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - frm) - timedelta(hours=24)) < timedelta(minutes=1)
    one_hour = hist(live, 1).get_json()
    assert 11 <= len(one_hour) <= 13   # 5-minute rows, cut to the hour asked


def test_history_is_extended_with_only_the_new_readings(live, airgradient):
    hist(live, 1)
    key = "ag:hist:1:24h"
    newest = max(sources._row_time(r) for r in sources._cache[key][1])
    age(key, sources.REFRESH_SECONDS + 1)
    hist(live, 1)
    past = [p for u, p in airgradient if u.endswith("/measures/past")]
    assert len(past) == 2
    frm = datetime.strptime(past[1]["from"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert frm == (newest - sources.HIST_OVERLAP).replace(microsecond=0)   # from the newest row, not 24 h back
    rows = sources._cache[key][1]
    times = [sources._row_time(r) for r in rows]
    assert times == sorted(set(times))   # merged without duplicates, oldest first


def test_merge_keeps_the_newer_row_and_drops_rows_past_the_day():
    t = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    old = [{"timestamp": stamp(t - timedelta(hours=25)), "pm02": 1}, {"timestamp": stamp(t), "pm02": 2}]
    new = [{"timestamp": stamp(t), "pm02": 5}, {"timestamp": stamp(t + timedelta(minutes=5)), "pm02": 6}]
    rows = sources.merge_rows(old, new, t - timedelta(hours=24))
    assert [r["pm02"] for r in rows] == [5, 6]


def test_a_stale_day_is_fetched_whole_again(live, airgradient):
    hist(live, 1)
    key = "ag:hist:1:24h"
    at, rows = sources._cache[key]
    old = [{**r, "timestamp": stamp(sources._row_time(r) - timedelta(days=2))} for r in rows]
    sources._cache[key] = (at - sources.REFRESH_SECONDS - 1, old)
    hist(live, 1)
    past = [p for u, p in airgradient if u.endswith("/measures/past")]
    frm = datetime.strptime(past[-1]["from"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - frm) - timedelta(hours=24)) < timedelta(minutes=1)


def test_the_week_is_asked_at_most_hourly(live, airgradient):
    hist(live, 168)
    age("ag:hist:1:168h", 30 * 60)
    hist(live, 168)
    assert len([u for u, _ in airgradient if u.endswith("/measures/past")]) == 1


def test_a_budget_trips_once_with_a_log_line(monkeypatch, caplog):
    monkeypatch.setitem(budgets.DAILY, "smoke", 3)
    for _ in range(3):
        budgets.spend("smoke")
    with caplog.at_level(logging.WARNING, logger="aq.budget"):
        for _ in range(4):
            with pytest.raises(budgets.BudgetExceeded):
                budgets.spend("smoke")
    tripped = [r for r in caplog.records if "budget tripped" in r.getMessage()]
    assert len(tripped) == 1 and "smoke" in tripped[0].getMessage()
    assert isinstance(budgets.BudgetExceeded("x"), RuntimeError)   # callers' error paths serve the cached answer


def test_a_spent_budget_stops_calls_to_airgradient(live, airgradient, monkeypatch):
    monkeypatch.setitem(budgets.DAILY, "ag_current", 1)
    assert live.get("/api/current").status_code == 200
    sources._cache.pop("ag:current", None)
    assert live.get("/api/current").status_code == 502
    assert len(airgradient) == 1


def test_a_refused_token_pauses_airgradient_for_half_an_hour(live, monkeypatch):
    calls = []

    def get(url, params=None, **kw):
        calls.append(url)
        return Resp({}, status=401)
    monkeypatch.setattr(sources.requests, "get", get)
    for _ in range(5):
        sources._cache.pop("ag:current", None)
        live.get("/api/current")
    hist(live, 1)
    assert len(calls) == 1
    assert budgets.usage()["paused"]["ag_current"] > 29 * 60
    assert budgets.usage()["paused"]["ag_history"] > 29 * 60


def test_a_failed_pollen_call_waits_ten_minutes(monkeypatch):
    calls = []
    monkeypatch.setenv("GOOGLE_POLLEN_API_KEY", "k")
    monkeypatch.setattr(sources, "env_key", lambda name: "k")
    monkeypatch.setattr(sources, "_fetch_pollen_raw", lambda *a, **k: calls.append(1) or {})
    for _ in range(3):
        with pytest.raises(RuntimeError):
            sources._fetch_pollen()
    assert len(calls) == 1
    assert budgets.usage()["paused"]["pollen"] > 9 * 60


def test_every_outside_api_has_a_budget():
    assert set(budgets.DAILY) == {"ag_current", "ag_history", "ag_map_area", "ag_map", "pollen", "weather",
                                  "air_quality", "smoke"}
    assert budgets.DAILY["ag_map_area"] == 2000
    assert sources.api_budget("/locations/7/measures/past") == "ag_history"
    assert sources.api_budget("/locations/measures/current") == "ag_current"


def test_usage_reports_counts_and_the_interval(live, airgradient):
    live.get("/api/current")
    body = live.get("/api/usage").get_json()
    assert body["used"]["ag_current"] == 1 and body["budget"]["ag_current"] == 5000
    assert body["refresh_seconds"] == sources.REFRESH_SECONDS


# ── Who is calling, and backing off (upstream.py) ──

class Answer:
    def __init__(self, payload=None, status=200, headers=None, text=""):
        self.payload, self.status_code, self.headers, self.text = payload, status, headers or {}, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self.payload


def test_every_outbound_call_says_who_is_calling(monkeypatch):
    import publicmap
    seen = []

    def get(url, params=None, headers=None, **kw):
        seen.append((url, (headers or {}).get("User-Agent", "")))
        if "pollen" in url:
            return Answer({"dailyInfo": []})
        if "open-meteo" in url:
            return Answer({"current": {"temperature_2m": 1}, "hourly": {"time": ["t"], "pm2_5": [1]}})
        if "nesdis" in url:
            return Answer(status=404)
        if url.endswith("/locations/7"):
            return Answer({"locationId": 7})
        if url.endswith("/locations/7/measures/current"):
            return Answer({"locationId": 7})
        if "map-data" in url:
            return Answer({"data": [], "total": None})
        return Answer([])
    monkeypatch.setattr(sources.requests, "get", get)
    here = sources.Here("home", 40.7, -74.0, "America/New_York")
    sources.api_get("/locations/measures/current")
    sources._fetch_weather(here)
    sources._fetch_air_forecast(here)
    with pytest.raises(RuntimeError):
        sources._fetch_smoke()
    sources._fetch_pollen_raw(40.7, -74.0, "k")
    publicmap.fetch_area(40.7, -74.0, 16)
    publicmap.fetch_place(7)
    publicmap.fetch_now(7)
    publicmap.history(7, 24)
    sources.validate_token("t")
    hosts = {u.split("/")[2] for u, _ in seen}
    assert {"api.airgradient.com", "api.open-meteo.com", "air-quality-api.open-meteo.com", "satepsanone.nesdis.noaa.gov",
            "pollen.googleapis.com", "map-data.airgradient.com"} <= hosts
    for url, agent in seen:
        assert agent == upstream.USER_AGENT, url
    assert upstream.USER_AGENT == f"HomeAir/{server.APP_VERSION.lstrip('v')}"


def test_a_429_pauses_every_airgradient_source_for_its_retry_after_and_alerts_once(monkeypatch, caplog):
    import publicmap
    calls = []
    monkeypatch.setattr(sources.requests, "get", lambda url, **kw: Answer({"data": []}))
    publicmap.fetch_area(40.7, -74.0, 16)   # the map: another host, not paused

    def get(url, **kw):
        calls.append(url)
        return Answer(status=429, headers={"Retry-After": "120"})
    monkeypatch.setattr(sources.requests, "get", lambda url, **kw: Answer([]))
    sources.api_get("/locations/1/measures/past")          # the host's other source, seen before
    monkeypatch.setattr(sources.requests, "get", get)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            sources.api_get("/locations/measures/current")
    paused = budgets.usage()["paused"]
    assert 110 < paused["ag_current"] <= 120 and 110 < paused["ag_history"] <= 120 and "ag_map_area" not in paused
    limited = budgets.usage()["rate_limited"]
    assert limited["ag_current"] == limited["ag_history"] == 1 and "ag_map_area" not in limited   # only that host's
    assert len([r for r in caplog.records if "ALERT rate limited" in r.getMessage()]) == 1
    n = len(calls)
    for path in ("/locations/measures/current", "/locations/1/measures/past"):
        with pytest.raises(budgets.BudgetExceeded):
            sources.api_get(path)
    assert len(calls) == n   # nothing more reaches AirGradient while it asked us to wait


def test_a_503_waits_for_retry_after_without_an_alert(monkeypatch):
    monkeypatch.setattr(sources.requests, "get", lambda url, **kw: Answer(status=503, headers={"Retry-After": "600"}))
    with pytest.raises(RuntimeError):
        sources.api_get("/locations/measures/current")
    assert 590 < budgets.usage()["paused"]["ag_current"] <= 600 and budgets.usage()["rate_limited"] == {}


def test_retry_after_reads_seconds_or_a_date_with_a_default_and_a_ceiling():
    from email.utils import format_datetime
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    assert budgets.retry_after("90") == 90
    assert budgets.retry_after(format_datetime(now + timedelta(minutes=5), usegmt=True), now.timestamp()) == 300
    assert budgets.retry_after(None) == budgets.retry_after("soon") == 15 * 60
    assert budgets.retry_after("999999") == 6 * 3600
    assert budgets.retry_after("-5") == 0


def test_failures_back_off_two_to_thirty_minutes_and_a_success_resets(monkeypatch):
    answers = []
    monkeypatch.setattr(sources.requests, "get", lambda url, **kw: answers.pop(0))
    waits = []
    for _ in range(6):
        answers.append(Answer(status=500))
        with pytest.raises(RuntimeError):
            sources.api_get("/locations/measures/current")
        waits.append(budgets.usage()["paused"]["ag_current"])
        budgets._paused.pop("ag_current")   # as if the wait were over
    assert [round(w / 60) for w in waits] == [2, 4, 8, 16, 30, 30]
    answers.append(Answer([]))
    sources.api_get("/locations/measures/current")
    answers.append(Answer(status=500))
    with pytest.raises(RuntimeError):
        sources.api_get("/locations/measures/current")
    assert round(budgets.usage()["paused"]["ag_current"] / 60) == 2   # back to the first step


def test_no_network_backs_off_too(monkeypatch):
    def down(url, **kw):
        raise requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(sources.requests, "get", down)
    with pytest.raises(RuntimeError):
        sources.api_get("/locations/measures/current")
    assert budgets.usage()["paused"]["ag_current"] > 100


def test_a_paused_source_is_logged_once_not_per_request(live, monkeypatch, caplog):
    def down(url, **kw):
        raise requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(sources.requests, "get", down)
    with caplog.at_level(logging.INFO):
        for _ in range(10):
            sources._cache.pop("ag:current", None)
            live.get("/api/current")
    lines = [r.getMessage() for r in caplog.records if "ag:current" in r.getMessage() or "ag_current" in r.getMessage()]
    assert len(lines) == 2   # the failure, and the back-off it starts; the nine requests after it make no call


# ── Nearby and the area estimate: one area call on AirGradient's map per place every 15 minutes ──

def near_home(i, km_north, **extra):
    """A public AirGradient monitor km_north km north of the reference home, as the map's area call gives it."""
    return {"locationId": 9000 + i, "locationName": f"Corner {i}", "latitude": server.HOME_LAT + km_north / 111.0,
            "longitude": server.HOME_LON, "pm25": 5.5, "rhum": 50, "measuredAt": stamp(datetime.now(timezone.utc)),
            "sensorType": "Small Sensor", "dataSource": "AirGradient", **extra}


@pytest.fixture
def area(live, monkeypatch):
    import publicmap
    rows = [near_home(1, 2), near_home(2, 5), near_home(3, 40), near_home(4, 0.05)]   # beyond 15 km; the home's own
    calls = []

    def get(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        if url == publicmap.AREA_URL:
            return Answer({"data": rows}, text="x" * 6_000)
        return Answer(status=404)
    monkeypatch.setattr(sources.requests, "get", get)
    monkeypatch.setattr(sources, "source_on", lambda name: True)
    yield live, calls


def area_key():
    return f"ag:area:{server.HOME_LAT:.2f},{server.HOME_LON:.2f}"


def test_nearby_is_one_area_call_per_fifteen_minutes(area):
    import publicmap
    c, calls = area
    first = c.get("/api/neighborhood").get_json()
    assert [n["locationId"] for n in first] == [9001, 9002]   # within 15 km, the home's own outdoor monitor left out
    assert all(n["pm02_corrected"] == 5.5 for n in first)   # the map's corrected PM2.5
    assert [n.get("publicContributorName") for n in first] == [None, None]   # the map gives no owner: none shown
    (url, params), = calls
    assert url == publicmap.AREA_URL and params["dataSources"] == "AirGradient" and params["measure"] == "pm25"
    span = sources.AREA_SEARCH_KM * 2 / publicmap.KM_PER_DEG
    assert params["ymax"] - params["ymin"] == pytest.approx(span, abs=1e-4)   # one box around the home, 16 km each way
    sources.area_monitors(server.HOME_LAT, server.HOME_LON)   # the area estimate reads the same cached call
    age(area_key(), sources.NEARBY_REFRESH_SECONDS - 5)
    c.get("/api/neighborhood")
    c.get("/api/neighborhood", headers={"X-Force-Refresh": "1"})
    assert len(calls) == 1   # all from the cache: Refresh doesn't hurry it either
    age(area_key(), 10)
    c.get("/api/neighborhood")
    assert len(calls) == 2   # one call once the 15 minutes have passed


def test_area_cache_is_fifteen_minutes_and_refresh_cannot_hurry_it():
    from demo import data as demo_data
    assert sources.AREA_CACHE["ttl"] == sources.AREA_CACHE["force_floor"] == sources.NEARBY_REFRESH_SECONDS == 15 * 60
    assert demo_data.PICKER_CACHE["ttl"] == demo_data.PICKER_CACHE["force_floor"] == 15 * 60


def test_nothing_asks_the_map_until_nearby_is_shown(area):
    c, calls = area
    c.get("/api/current")
    c.get("/api/context")
    assert not [u for u, _ in calls if "map-data" in u]


def test_concurrent_requests_share_one_area_call(area, monkeypatch):
    import threading
    import time
    c, calls = area
    real = sources.requests.get

    def slow(url, **kw):
        time.sleep(0.2)
        return real(url, **kw)
    monkeypatch.setattr(sources.requests, "get", slow)
    threads = [threading.Thread(target=sources.area_monitors, args=(server.HOME_LAT, server.HOME_LON)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
