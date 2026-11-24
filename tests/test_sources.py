import sqlite3
"""Sources that are off or not set up: each says so, calmly, and the summary drops what is missing."""
import pytest

import server
import sources
from conftest import SAMPLE_PAYLOAD
from test_golden import config_from, strings


@pytest.fixture
def use_config():
    before = server.CFG
    yield server.use_config
    server.use_config(before)


def status(client):
    return client.get("/api/context").get_json()["status"]


def test_pollen_without_key_is_not_set_up(monkeypatch, client):
    monkeypatch.delenv("GOOGLE_POLLEN_API_KEY", raising=False)
    assert status(client)["pollen"] == "not_set_up"


def test_pollen_with_key_is_ok_or_error(monkeypatch, client):
    monkeypatch.setenv("GOOGLE_POLLEN_API_KEY", "test-key")
    assert status(client)["pollen"] == "error"   # on, set up, and nothing came back
    monkeypatch.setattr(sources, "get_pollen_days", lambda *a, **k: {"today": [{"name": "Grass", "index": 1}], "tomorrow": []})
    assert status(client)["pollen"] == "ok"


def test_pollen_turned_off_is_off_and_never_fetched(monkeypatch, client, use_config):
    monkeypatch.setenv("GOOGLE_POLLEN_API_KEY", "test-key")
    use_config(config_from(sources={"pollen": False}))

    def boom(*a, **k):
        raise AssertionError("pollen was fetched")
    monkeypatch.setattr(sources, "get_pollen_days", boom)
    assert status(client)["pollen"] == "off"


def test_smoke_off_in_config(monkeypatch, client, use_config):
    use_config(config_from(sources={"smoke": False}))

    def boom(*a, **k):
        raise AssertionError("smoke was fetched")
    monkeypatch.setattr(sources, "smoke_status", boom)
    assert status(client)["smoke"] == "off"


def test_smoke_on_is_ok_when_checked_error_when_not(monkeypatch, client):
    monkeypatch.setattr(sources, "smoke_status", lambda: {"checked": True, "detected": False, "density": None, "date": "2026-11-08"})
    assert status(client)["smoke"] == "ok"
    monkeypatch.setattr(sources, "smoke_status", lambda: {"checked": False, "detected": False, "density": None, "date": None})
    assert status(client)["smoke"] == "error"


def test_no_road_is_off(client, use_config):
    assert status(client)["road"] == "ok"
    use_config(config_from(road=None))
    assert status(client)["road"] == "off"


def test_nearby_off_in_config(monkeypatch, client, use_config):
    use_config(config_from(sources={"nearby": False}))

    def boom(*a, **k):
        raise AssertionError("nearby was fetched")
    monkeypatch.setattr(sources, "_fetch_nearby", boom)
    assert status(client)["nearby"] == "off"
    assert client.get("/api/neighborhood").get_json() == []


def test_summary_without_pollen_uses_humidity_only(monkeypatch, client, use_config):
    monkeypatch.delenv("GOOGLE_POLLEN_API_KEY", raising=False)
    high = [{"name": "Grass", "level": "High", "index": 4, "in_season": True}]
    # even if a stale cache held pollen, a source that is not set up is not read
    monkeypatch.setattr(sources, "get_pollen_data", lambda *a, **k: high)
    monkeypatch.setattr(sources, "get_pollen_days", lambda *a, **k: {"today": high, "tomorrow": high})
    body = {"format": "read", **SAMPLE_PAYLOAD, "indoor_humidity": 45}
    out = client.post("/api/analyze", json=body).get_json()
    profiles = out["summary"]["profiles"]
    sinus = next(n for n in profiles if n["id"] == "sinus")
    assert sinus["facts"]["pollen_index"] is None
    assert all(i["id"] != "pollen" for i in out["summary"]["items"])
    said = list(strings(out["summary"])) + list(strings(out["profiles"]))
    assert not any("{" in s or "  " in s for s in said)
    assert not any("pollen" in s.lower() for s in strings(sinus))


# --- the read order: the Wi-Fi, then the cloud, then the last reading ---

SERIALS = {"indoor": "84fce6000001", "outdoor": "84fce6000002"}


class StubPoller:
    """What localnet.Poller shows the server: a state and the last row per kind."""
    def __init__(self, states):
        self.states = states

    def state(self, kind):
        return self.states[kind]

    def latest(self, kind):
        return {"locationType": kind, "locationId": {"indoor": 1, "outdoor": 2}[kind], "serialno": SERIALS[kind], "rco2": 600, "pm02_corrected": 3.0,
                "timestamp": "2026-11-22T10:00:00Z", "via": "local"}

    def others(self):
        return {"indoor": [{"serial_end": "3333", "model": "I-9PSL"}]}


@pytest.fixture
def wifi(monkeypatch, client):
    """The reference home with a serial for each monitor and a poller whose states a test sets."""
    import dataclasses
    place = dataclasses.replace(server.CFG.place, **{f"{k}_serial": s for k, s in SERIALS.items()})
    monkeypatch.setattr(server, "CFG", dataclasses.replace(server.CFG, place=place))
    monkeypatch.setattr(sources, "_MONITOR_VIA", {})

    def use(states, token="SECRET123"):
        monkeypatch.setattr(server, "POLLER", StubPoller(states))
        monkeypatch.setattr(server, "TOKEN", token)
        monkeypatch.setattr(server, "MODE", "live" if token else "local")
        return client
    return use


def test_all_local_makes_no_cloud_call(wifi, monkeypatch):
    """check 3: with every monitor answering on the Wi-Fi the cloud is never asked (AirGradient's load, the token's quota)."""
    def boom(*a, **k):
        raise AssertionError("the cloud was asked")
    monkeypatch.setattr(sources, "api_get", boom)
    rows = wifi({"indoor": "local", "outdoor": "local"}).get("/api/current").get_json()
    assert [r["via"] for r in rows] == ["local", "local"]


def test_a_missed_read_falls_back_to_cloud_then_stale(wifi, monkeypatch):
    """The card is never blank: a monitor that missed reads shows the cloud's row, or without a token its last row marked stale."""
    cloud = [{"locationId": 7, "locationType": "indoor", "serialno": SERIALS["indoor"].upper(), "rco2": 900, "timestamp": "2026-11-22T09:58:00.000Z"}]
    monkeypatch.setattr(sources, "api_get", lambda *a, **k: cloud)
    rows = wifi({"indoor": "stale", "outdoor": "local"}).get("/api/current").get_json()
    assert [(r["locationType"], r["via"], r["rco2"]) for r in rows] == [("indoor", "cloud", 900), ("outdoor", "local", 600)]
    sources._cache.clear()
    rows = wifi({"indoor": "stale", "outdoor": "local"}, token="").get("/api/current").get_json()
    assert [(r["locationType"], r.get("stale"), r["rco2"]) for r in rows] == [("indoor", True, 600), ("outdoor", None, 600)]


def test_health_names_kinds_only(wifi):
    """check 19: /health says where each monitor is read from and never an address or a serial."""
    import json
    import re
    body = wifi({"indoor": "local", "outdoor": "local"}, token="").get("/health").get_json()
    assert body["monitors"] == {"indoor": "local", "outdoor": "local"}
    assert body["also_found"] == {"indoor": [{"serial_end": "3333", "model": "I-9PSL"}]}   # for "Your monitors"' switch
    text = json.dumps(body)
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", text) and not any(s in text for s in SERIALS.values())


def test_health_follows_the_poller_when_no_page_asks(wifi, monkeypatch):
    """--live and --status read /health: a monitor that stopped answering must not stay "on your Wi-Fi" because no
    page has asked /api/current since it did."""
    client = wifi({"indoor": "local", "outdoor": "local"}, token="")
    client.get("/api/current")   # a page asked while both answered
    assert client.get("/health").get_json()["monitors"] == {"indoor": "local", "outdoor": "local"}
    server.POLLER.states["indoor"] = "stale"   # its reads stop; no page asks again
    assert client.get("/health").get_json()["monitors"] == {"indoor": "stale", "outdoor": "local"}
    monkeypatch.setattr(server, "TOKEN", "SECRET123")
    assert client.get("/health").get_json()["monitors"] == {"indoor": "cloud", "outdoor": "local"}


def test_importing_the_reference_config_starts_nothing():
    """The tests' config has no serial: no poller, so no test browses the Wi-Fi."""
    assert server.POLLER is None


# --- the home's own history ---

def stored(tmp_path, monkeypatch, serial="indoor", minutes=range(0, 60, 5)):
    """A history store with one reading every 5 minutes up to 10:00 UTC on 22 Nov, for one monitor."""
    from datetime import datetime, timedelta, timezone

    from history_store import HistoryStore
    store = HistoryStore(tmp_path / "history.sqlite")
    start = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)
    for m in minutes:
        store.add(SERIALS[serial], {"timestamp": (start + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "rco2": 600 + m, "pm02_corrected": 3.0, "rhum": 40, "atmp": 21.5, "serialno": SERIALS[serial]})
    monkeypatch.setattr(server, "STORE", store)
    return store


def test_history_comes_from_the_store_by_local_id(wifi, monkeypatch, tmp_path):
    """check 9: a home with no token and no cloud id charts its own readings, by the fixed local id, and never asks the cloud."""
    stored(tmp_path, monkeypatch)
    monkeypatch.setattr(sources, "api_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the cloud was asked")))
    client = wifi({"indoor": "local", "outdoor": "local"}, token="")
    rows = client.get("/api/history/1?from=2026-11-22T09:00:00Z&to=2026-11-22T10:00:00Z").get_json()
    assert [r["rco2"] for r in rows] == [600 + m for m in range(0, 60, 5)]
    assert set(rows[0]) <= set(sources.HIST_KEEP)   # the temperature is kept in the store, not sent to the charts


def test_after_reads_stop_the_chart_goes_on_from_the_cloud_or_ends(wifi, monkeypatch, tmp_path):
    """Reads stopped an hour ago (the monitor left the Wi-Fi): with a token the last hour comes from the cloud after
    the stored rows, which stay as they were; without one the chart ends at the last reading. The store alone would
    answer a stale chart that looks current."""
    import dataclasses
    from datetime import datetime, timedelta, timezone

    from history_store import HistoryStore
    client = wifi({"indoor": "stale", "outdoor": "stale"})
    monkeypatch.setattr(server, "CFG", dataclasses.replace(server.CFG, place=dataclasses.replace(server.CFG.place, indoor_id=7)))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    store = HistoryStore(tmp_path / "history.sqlite")
    for m in range(120, 59, -5):   # a reading every 5 min from 2 h ago until 1 h ago
        store.add(SERIALS["indoor"], {"timestamp": (now - timedelta(minutes=m)).strftime(fmt), "rco2": 600})
    monkeypatch.setattr(server, "STORE", store)
    asked = []

    def cloud(path, params=None):
        asked.append(path)
        frm, to = (datetime.fromisoformat(params[k].replace("Z", "+00:00")) for k in ("from", "to"))
        steps = int((to - frm) / timedelta(minutes=5))
        return [{"timestamp": (frm + i * timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "rco2": 900} for i in range(steps + 1)]
    monkeypatch.setattr(sources, "api_get", cloud)
    sources._cache.clear()
    span = f"from={(now - timedelta(hours=2)).strftime(fmt)}&to={now.strftime(fmt)}"
    rows = client.get(f"/api/history/7?{span}").get_json()
    stamps = [datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) for r in rows]
    assert [r["rco2"] for r in rows[:13]] == [600] * 13 and stamps == sorted(stamps)
    last_hour = [r for r, t in zip(rows, stamps) if t > now - timedelta(minutes=60)]
    assert len(last_hour) >= 11 and {r["rco2"] for r in last_hour} == {900}
    assert asked == ["/locations/7/measures/past"]
    sources._cache.clear()
    asked.clear()
    monkeypatch.setattr(server, "TOKEN", "")
    rows = client.get(f"/api/history/7?{span}").get_json()
    assert [r["rco2"] for r in rows] == [600] * 13 and not asked


def test_a_restart_shows_the_last_stored_reading_as_stale(wifi, monkeypatch, tmp_path):
    """The poller's memory is empty after a restart: the card still shows the last stored reading, marked stale, not a blank."""
    stored(tmp_path, monkeypatch)
    client = wifi({"indoor": "stale", "outdoor": "stale"}, token="")
    monkeypatch.setattr(server.POLLER, "latest", lambda kind: None)
    rows = client.get("/api/current").get_json()
    assert [(r["locationType"], r["stale"], r["rco2"], r["serialno"]) for r in rows] == [("indoor", True, 655, SERIALS["indoor"])]


# --- the cloud fill of the last 7 days ---

class InlinePool:
    def submit(self, fn, *args):
        fn(*args)


def fill_home(wifi, monkeypatch, tmp_path, token="SECRET123"):
    """Two monitors with cloud ids, each with stored rows leaving two gaps in the week; fills run inline."""
    import dataclasses
    from datetime import datetime, timedelta, timezone

    from history_store import HistoryStore
    client = wifi({"indoor": "local", "outdoor": "local"}, token=token)
    place = dataclasses.replace(server.CFG.place, indoor_id=7, outdoor_id=8)
    monkeypatch.setattr(server, "CFG", dataclasses.replace(server.CFG, place=place))
    store = HistoryStore(tmp_path / "history.sqlite")
    now = datetime.now(timezone.utc)
    for serial in SERIALS.values():   # rows at 2 d and 1 d ago only: a gap before, between and after them
        for ago in (timedelta(days=2), timedelta(days=1)):
            store.add(serial, {"timestamp": (now - ago).strftime("%Y-%m-%dT%H:%M:%SZ"), "rco2": 500, "atmp": 21.0})
    monkeypatch.setattr(server, "STORE", store)
    monkeypatch.setattr(sources, "_REFRESH_POOL", InlinePool())
    monkeypatch.setattr(sources, "_asked_until", {})
    return client, store, now


def test_fill_makes_one_past_call_per_monitor(wifi, monkeypatch, tmp_path):
    """check 12: a token fills the week with exactly one /measures/past call per monitor (AirGradient's load), spanning
    all its gaps, and the local rows already stored are not touched (the fill writes into the only copy of the history)."""
    from datetime import timedelta
    _, store, now = fill_home(wifi, monkeypatch, tmp_path)
    calls = []

    def past(location_id, frm, to, keep=sources.HIST_KEEP):
        calls.append((location_id, frm, to))
        return [{"timestamp": (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "rco2": 999}]
    monkeypatch.setattr(sources, "_past", past)
    before = {s: store.rows(s, now - timedelta(days=7), now) for s in SERIALS.values()}
    sources.start_fill("test")
    assert sorted(c[0] for c in calls) == [7, 8]
    assert all(frm < now - timedelta(days=6) and to > now - timedelta(minutes=1) for _, frm, to in calls)
    for serial, rows in before.items():
        after = store.rows(serial, now - timedelta(days=7), now)
        assert [r for r in after if r["rco2"] != 999] == rows and len(after) == len(rows) + 1


def test_a_later_fill_asks_only_from_where_the_last_one_ended(wifi, monkeypatch, tmp_path):
    """Every resume after a long gap starts a fill: asking the whole week each time (the gaps the cloud had nothing for
    stay gaps) would cost AirGradient a week of rows per monitor per drop-out, unseen. One week at start, then only the
    time since, less a margin for rows the cloud got late."""
    from datetime import timedelta
    _, store, now = fill_home(wifi, monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(sources, "_past", lambda location_id, frm, to, keep=None: calls.append((location_id, frm, to)) or [])
    sources.start_fill("start")
    first_to = {loc: to for loc, _, to in calls}
    calls.clear()
    sources.start_fill("resume")
    assert sorted(c[0] for c in calls) == [7, 8]
    assert all(frm >= first_to[loc] - sources.history_store.GAP and to >= first_to[loc] for loc, frm, to in calls)


def test_a_fill_whose_write_failed_asks_the_whole_range_again(wifi, monkeypatch, tmp_path):
    """A write that fails (the database locked) must not count as answered: the next fill asks the same week again,
    or the unsaved history stays missing until a restart (Codex review, 22 Nov)."""
    from datetime import timedelta
    _, store, now = fill_home(wifi, monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(sources, "_past", lambda location_id, frm, to, keep=None: calls.append((frm, to)) or [])
    real_fill = store.fill
    monkeypatch.setattr(store, "fill", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    sources.start_fill("start")
    monkeypatch.setattr(store, "fill", real_fill)
    calls.clear()
    sources.start_fill("retry")
    assert calls and all(frm < now - timedelta(days=6) for frm, _ in calls)


def test_no_token_no_fill(wifi, monkeypatch, tmp_path):
    """Without a token the cloud is never asked for the week."""
    fill_home(wifi, monkeypatch, tmp_path, token="")
    monkeypatch.setattr(sources, "_past", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the cloud was asked")))
    sources.start_fill("test")
