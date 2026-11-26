"""The check on a timer: it reads what the page reads, sends once, and says nothing when it can't read."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import agent_tools
import alerts
import alert_rules
from alert_rules import OFFLINE_AFTER, Phone, Sent
from alert_store import AlertStore
from demo.moments import MOMENTS
from test_demo import c, demo, read  # noqa: F401  (the demo app's fixtures)

NY = ZoneInfo("America/New_York")
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CO2 = {"id": "co2", "label": "CO₂ inside", "num": "1,240", "unit": "ppm", "ctx": "55% over the 800 limit", "facts": {},
       "action": {"text": "Open a window for 10 min", "when": "now", "when_end": None}}


@pytest.mark.parametrize("moment", sorted(MOMENTS))
def test_page_and_alert_path_agree(c, moment):
    """The alerts are built from the same Summary the page shows: same ids, action texts and times."""
    def fetch(path, body=None):
        path += ("&" if "?" in path else "?") + f"moment={moment}"
        r = c.post(path, json=body) if body is not None else c.get(path)
        return r.get_json()
    page = read(c, moment)["summary"]["items"]
    alert = agent_tools.read_now(fetch)["read"]["summary"]["items"]
    pick = lambda items: [(i["id"], (i.get("action") or {}).get("text"), (i.get("action") or {}).get("when")) for i in items]
    assert pick(alert) == pick(page)


class Fake:
    """A checker with the app's answers replaced by canned ones, and a push that records what it was asked."""
    def __init__(self, tmp_path, items=(CO2,), age=timedelta(minutes=2), result="ok", store=None):
        self.store = store or AlertStore(tmp_path / "alerts.sqlite")
        self.items, self.age, self.result, self.pushed = list(items), age, result, []
        self.current = None
        self.checker = self.new()

    def new(self):
        ck = alerts.Checker(None, self.store, send=self.push, tz=NY)
        ck._fetch = self.fetch
        return ck

    def push(self, sub, title, body, url="/#summary"):
        self.pushed.append((sub["endpoint"], title))
        return self.result

    def fetch(self, path, body=None):
        if path == "/api/current":
            if self.current is not None:
                return self.current
            return [{"locationType": "indoor", "locationId": 1, "timestamp": (T0 - self.age).isoformat()},
                    {"locationType": "outdoor", "locationId": 2, "timestamp": (T0 - self.age).isoformat()}]
        return {"summary": {"items": self.items}}

    def phone(self, endpoint="https://push.example/a", since=T0 - timedelta(days=1), baseline=False):
        """A phone added `since`; with its baseline taken already unless `baseline` (one just added)."""
        phone_id, _ = self.store.add_phone({"endpoint": endpoint, "keys": {}}, "Phone", since)
        if not baseline:
            self.store.take_baselines(lambda phone: None)
        return phone_id


def test_sends_once_and_not_again_after_a_restart(tmp_path):
    f = Fake(tmp_path)
    f.phone()
    assert f.checker.check_once(T0) == 1
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 0
    assert f.new().check_once(T0 + timedelta(minutes=4)) == 0   # a restart: what was sent is in the store
    assert len(f.pushed) == 1


def test_failed_read_sends_nothing(tmp_path):
    f = Fake(tmp_path, age=OFFLINE_AFTER * 3)   # would be Offline if the rows were read
    f.phone()
    f.current = {"error": "AirGradient answered HTTP 502"}
    assert f.checker.check_once(T0) == 0
    f.current = []
    assert f.checker.check_once(T0) == 0
    assert f.pushed == []


def test_a_monitor_silent_for_an_hour_is_offline(tmp_path):
    f = Fake(tmp_path, items=[], age=OFFLINE_AFTER + timedelta(minutes=1))
    f.phone()
    assert f.checker.check_once(T0) == 1   # both Offline monitors in one notification
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 0


def test_gone_phone_removed(tmp_path):
    f = Fake(tmp_path, result="gone")
    f.phone()
    assert f.checker.check_once(T0) == 0
    assert f.store.listing() == []


def test_failed_push_is_tried_again(tmp_path):
    f = Fake(tmp_path, result="error")
    f.phone()
    assert f.checker.check_once(T0) == 0
    f.result = "ok"
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 1


def req(url, **headers):
    host = url.split("//")[1].split("/")[0]
    return SimpleNamespace(is_secure=url.startswith("https"), host=host, headers=headers)


def test_available_states(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    assert alerts.available(req("https://homeair.up.railway.app/")) == "online"
    assert alerts.available(req("http://homeair.up.railway.app/", **{"X-Forwarded-Proto": "https"})) == "online"
    assert alerts.available(req("http://homeair.up.railway.app/")) == "local"
    assert alerts.available(req("http://localhost:5555/")) == "local"
    assert alerts.available(req("https://192.168.1.5/")) == "local"
    assert alerts.available(req("https://[::1]:5555/")) == "local"
    assert alerts.available(req("https://mac.local/")) == "local"
    assert alerts.available(req("https://homeair.up.railway.app/"), demo=True) == "demo"
    assert alerts.available(req("https://homeair.up.railway.app/"), reading=False) == "local"   # nothing to read: no checker
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    assert alerts.available(req("https://homeair.up.railway.app/")) == "local"


def test_gone_phone_next_to_a_sent_phone_keeps_what_was_sent(tmp_path):
    """A phone that answers gone, next to a phone that was sent to: no error, and the next check sends nothing again."""
    f = Fake(tmp_path, age=OFFLINE_AFTER + timedelta(minutes=1))
    a = f.phone("https://push.example/a")
    b = f.phone("https://push.example/b")
    f.push = lambda sub, title, body, url="/#summary": (f.pushed.append((sub["endpoint"], title)),
                                                         "gone" if sub["endpoint"].endswith("/b") else "ok")[1]
    f.checker.send = f.push
    assert f.checker.check_once(T0) == 1   # a: the action and both Offline alerts in one notification
    assert [p["id"] for p in f.store.listing()] == [a]
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 0
    assert len([1 for e, _ in f.pushed if e.endswith("/a")]) == 1


def test_phone_turned_off_between_read_and_send(tmp_path):
    f = Fake(tmp_path)
    f.phone()
    real = f.store.subscription
    def vanish(phone_id):
        raise KeyError(phone_id)
    f.store.subscription = vanish
    assert f.checker.check_once(T0) == 0
    f.store.subscription = real


# ---- the routes ----

import json as _json

from flask import Flask

import alert_store as store_module

ONLINE = "https://homeair.up.railway.app"
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/a", "keys": {"p256dh": "pk", "auth": "ak"}}
IPHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit"}


class Routes:
    """The alert routes on a bare Flask app: a canned Summary read, a push that records what it was asked."""
    def __init__(self, tmp_path, monkeypatch, items=(CO2,), demo=False):
        monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
        monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
        self.store = AlertStore(tmp_path / "alerts.sqlite")
        self.items, self.pushed, self.now = (list(items) if items is not None else None), [], T0
        self.app = Flask(__name__)
        alerts.init(self.app, lambda: self.store, demo=lambda: demo, tz=NY, clock=lambda: self.now,
                    send=lambda sub, title, body, url="/#summary": self.pushed.append((sub["endpoint"], title, body)) or "ok")
        self.c = self.app.test_client()

    def call(self, method, path, body=None, base=ONLINE, **headers):
        return getattr(self.c, method)(path, json=body, base_url=base, headers=headers)

    def add(self, sub=SUB, **headers):
        return self.call("post", "/api/alerts/phones", {"subscription": sub, "device": "x"}, **headers)


@pytest.fixture
def rt(tmp_path, monkeypatch):
    return Routes(tmp_path, monkeypatch)


def test_subscribe_returns_secret_and_sends_test(rt):
    r = rt.add(**IPHONE)
    assert r.status_code == 201 and r.headers["Cache-Control"] == "no-store"
    got = r.get_json()
    assert got["id"] and got["secret"] and got["since"] == "2026-11-20"
    assert rt.pushed == [("https://fcm.googleapis.com/fcm/send/a", "Test: alerts work on this phone",
                          "Home Air sends each action here when it is due. No alerts from 10 PM to 7 AM.")]
    assert rt.store.listing()[0]["device"] == "iPhone"
    # the route reads nothing: the next check takes the phone's baseline
    phone = rt.store.phones()[0]
    assert phone.sent == {} and phone.baseline == {alert_rules.BASELINE_ALL}


def test_unknown_device_is_phone_and_android_says_this_phone(rt):
    rt.add(**{"User-Agent": "Mozilla/5.0 (Linux; Android 15) Chrome/130"})
    assert rt.store.listing()[0]["device"] == "Android phone" and "this phone" in rt.pushed[0][1]
    rt.add({**SUB, "endpoint": "https://fcm.googleapis.com/fcm/send/b"}, **{"User-Agent": "Firefox"})
    assert sorted(d["device"] for d in rt.store.listing()) == ["Android phone", "Phone"]


def test_limit_409(rt):
    for n in range(store_module.MAX_PHONES):
        assert rt.add({**SUB, "endpoint": f"https://fcm.googleapis.com/fcm/send/{n}"}).status_code == 201
    r = rt.add({**SUB, "endpoint": "https://fcm.googleapis.com/fcm/send/extra"})
    assert r.status_code == 409 and r.get_json() == {"error": "limit"}
    assert len(rt.pushed) == store_module.MAX_PHONES


def test_patch_needs_secret(rt):
    got = rt.add().get_json()
    path = f"/api/alerts/phones/{got['id']}"
    for body in ({"muted": ["co2"], "quiet": [22, 7]}, {"secret": "", "muted": ["co2"]}, {"secret": "wrong", "muted": ["co2"]}):
        assert rt.call("patch", path, body).status_code == 403
    assert rt.store.phones()[0].muted == frozenset()
    assert rt.call("patch", path, {"secret": got["secret"], "muted": ["co2"], "quiet": [23, 6]}).status_code == 204
    p = rt.store.phones()[0]
    assert p.muted == {"co2"} and p.quiet == (23, 6)
    assert rt.call("patch", path, {"secret": got["secret"], "muted": ["co2"], "quiet": [25, 6]}).status_code == 400


def test_choices_read_needs_the_secret_and_gives_the_stored_values(rt):
    got = rt.add().get_json()
    path = f"/api/alerts/phones/{got['id']}/choices"
    for body in ({}, {"secret": ""}, {"secret": "wrong"}):
        assert rt.call("post", path, body).status_code == 403
    assert rt.call("post", "/api/alerts/phones/nobody/choices", {"secret": got["secret"]}).status_code == 403
    assert rt.call("post", path, {"secret": got["secret"]}, Origin="https://evil.example").status_code == 403
    r = rt.call("post", path, {"secret": got["secret"]})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert r.get_json() == {"muted": [], "quiet": [22, 7]}
    rt.call("patch", f"/api/alerts/phones/{got['id']}", {"secret": got["secret"], "muted": ["rh", "co2"], "quiet": [23, 6]})
    assert rt.call("post", path, {"secret": got["secret"]}).get_json() == {"muted": ["co2", "rh"], "quiet": [23, 6]}
    assert rt.call("post", path, {"secret": got["secret"]}, base="http://localhost:5555").status_code == 403


def test_unmuting_waits_for_that_items_baseline(rt):
    got = rt.add().get_json()
    rt.store.take_baselines(lambda phone: None)   # the check after turning alerts on
    path = f"/api/alerts/phones/{got['id']}"
    rt.call("patch", path, {"secret": got["secret"], "muted": ["co2", "rh"]})
    assert rt.store.phones()[0].baseline == frozenset()
    rt.call("patch", path, {"secret": got["secret"], "muted": ["rh"]})
    phone = rt.store.phones()[0]
    assert phone.muted == {"rh"} and phone.baseline == {"co2"} and phone.sent == {}


def test_delete_and_test_need_the_secret(rt):
    got = rt.add().get_json()
    path = f"/api/alerts/phones/{got['id']}"
    rt.pushed.clear()
    assert rt.call("post", path + "/test", {}).status_code == 403
    assert rt.call("post", path + "/test", {"secret": ""}).status_code == 403
    assert rt.pushed == []
    assert rt.call("post", path + "/test", {"secret": got["secret"]}).status_code == 202 and len(rt.pushed) == 1
    for body in ({"secret": ""}, {"secret": "wrong"}):   # a secret given must be this phone's
        assert rt.call("delete", path, body).status_code == 403 and len(rt.store.listing()) == 1
    rt.pushed.clear()
    assert rt.call("delete", path, {"secret": got["secret"]}).status_code == 204 and rt.store.listing() == []
    assert rt.pushed == []   # a phone turning itself off gets no notice


def test_local_and_demo_refuse_writes(tmp_path, monkeypatch):
    rt = Routes(tmp_path, monkeypatch)
    assert rt.call("post", "/api/alerts/phones", {"subscription": SUB}, base="http://localhost:5555").status_code == 403
    assert rt.call("post", "/api/alerts/phones", {"subscription": SUB}, base="http://homeair.up.railway.app").status_code == 403
    demo = Routes(tmp_path / "d", monkeypatch, demo=True)
    assert demo.add().status_code == 403
    assert demo.call("get", "/api/alerts").get_json()["state"] == "demo"
    assert rt.call("get", "/api/alerts", base="http://localhost:5555").get_json() == {
        "state": "local", "key": None, "phones": [], "max": store_module.MAX_PHONES}
    assert rt.store.listing() == [] and rt.pushed == []


def test_cross_origin_refused(rt):
    assert rt.add(Origin="https://evil.example").status_code == 403
    assert rt.add(Origin="null").status_code == 403
    assert rt.add(Origin=ONLINE).status_code == 201
    assert rt.store.listing() and len(rt.pushed) == 1


def test_bad_bodies(rt):
    assert rt.call("post", "/api/alerts/phones", {"subscription": {**SUB, "endpoint": "http://push.example/a"}}).status_code == 400
    assert rt.call("post", "/api/alerts/phones", {"subscription": {"endpoint": SUB["endpoint"], "keys": {"auth": "a"}}}).status_code == 400
    assert rt.call("post", "/api/alerts/phones", {"subscription": {**SUB, "keys": {"p256dh": 1, "auth": "a"}}}).status_code == 400
    assert rt.call("post", "/api/alerts/phones", [1]).status_code == 400
    big = {"subscription": {**SUB, "endpoint": "https://fcm.googleapis.com/fcm/send/" + "a" * 5000}}
    assert rt.call("post", "/api/alerts/phones", big).status_code == 413
    assert rt.c.post("/api/alerts/phones", data=b"{}", base_url=ONLINE, headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
                     environ_overrides={"CONTENT_LENGTH": ""}).status_code == 411
    assert rt.store.listing() == []


def test_listing_shows_no_endpoint(rt):
    got = rt.add().get_json()
    text = rt.call("get", "/api/alerts").get_data(as_text=True)
    body = _json.loads(text)
    assert body["state"] == "online" and body["key"] == "pub" and body["max"] == store_module.MAX_PHONES
    assert body["phones"] == [{"id": got["id"], "device": "Phone", "since": "2026-11-20", "time": "07:00"}]   # noon UTC is 7 AM in New York
    assert "fcm.googleapis.com" not in text and got["secret"] not in text and "ak" not in body["phones"][0].values()


@pytest.mark.parametrize("endpoint", [
    "https://push.example/a", "https://localhost/a", "https://127.1/a", "https://2130706433/a", "https://localhost./a",
    "https://foo.railway.internal/a", "https://100.100.1.1/a", "https://fcm.googleapis.com.evil.example/a",
    "https://notfcm.googleapis.com/a", "https://evil.example@fcm.googleapis.com.evil.example/a",
    "http://fcm.googleapis.com/a", "https://fcm.googleapis.com:bad/a",
    "https://127.0.0.1\\@fcm.googleapis.com/x", "https://evil.com\\@fcm.googleapis.com/x",
    "https://user@fcm.googleapis.com/x", "https://fcm.googleapis.com#@evil.example/x"])
def test_only_push_services_are_accepted(rt, endpoint):
    r = rt.add({**SUB, "endpoint": endpoint})
    assert r.status_code == 400 and r.get_json()["error"] in ("push service", "bad subscription")
    assert rt.store.listing() == [] and rt.pushed == []


def test_an_unlisted_push_service_is_named(rt):
    """The page tells this one apart from a broken request: tapping again won't help, another browser will."""
    assert rt.add({**SUB, "endpoint": "https://push.example/a"}).get_json() == {"error": "push service"}
    assert rt.call("post", "/api/alerts/phones", {"subscription": {**SUB, "keys": {"p256dh": 1, "auth": "a"}}}).get_json() == {"error": "bad subscription"}


@pytest.mark.parametrize("endpoint", [
    "https://web.push.apple.com/QGxx", "https://fcm.googleapis.com/fcm/send/abc",
    "https://updates.push.services.mozilla.com/wpush/v2/abc", "https://FCM.googleapis.com/x"])
def test_each_push_service_is_accepted(rt, endpoint):
    assert rt.add({**SUB, "endpoint": endpoint}).status_code == 201


def test_no_store_through_the_real_app(c):
    r = c.get("/api/alerts")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store" and r.get_json()["state"] == "demo"


def routes_with(tmp_path, monkeypatch, result):
    r = Routes(tmp_path, monkeypatch)
    app = Flask(__name__)
    alerts.init(app, lambda: r.store, tz=NY, clock=lambda: r.now, send=lambda *a, **k: result)
    r.c = app.test_client()
    return r


def test_error_keeps_the_phone_gone_drops_it(tmp_path, monkeypatch):
    r = routes_with(tmp_path, monkeypatch, "error")
    got = r.add()
    assert got.status_code == 201 and got.get_json()["sent"] is False and len(r.store.listing()) == 1
    path = f"/api/alerts/phones/{got.get_json()['id']}"
    t = r.call("post", path + "/test", {"secret": got.get_json()["secret"]})
    assert t.status_code == 502 and t.get_json() == {"sent": False}
    g = routes_with(tmp_path / "g", monkeypatch, "gone")
    added = g.add()
    assert added.status_code == 410 and added.get_json() == {"sent": False, "gone": True} and g.store.listing() == []


def test_test_uses_the_phones_quiet_hours(rt):
    got = rt.add().get_json()
    path = f"/api/alerts/phones/{got['id']}"
    rt.call("patch", path, {"secret": got["secret"], "quiet": [23, 6]})
    rt.call("post", path + "/test", {"secret": got["secret"]})
    assert rt.pushed[-1][2].endswith("No alerts from 11 PM to 6 AM.")


def test_since_is_the_homes_date(rt):
    rt.now = datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc)   # 9 PM on the 20th in New York
    assert rt.add().get_json()["since"] == "2026-11-20"
    assert rt.store.listing()[0]["since"] == "2026-11-20" and rt.store.listing()[0]["time"] == "21:00"


def test_home_air_with_nothing_to_read_accepts_no_phone(tmp_path, monkeypatch):
    r = Routes(tmp_path, monkeypatch)
    app = Flask(__name__)
    alerts.init(app, lambda: r.store, reading=lambda: False, tz=NY)
    c2 = app.test_client()
    assert c2.post("/api/alerts/phones", json={"subscription": SUB}, base_url=ONLINE).status_code == 403
    assert c2.get("/api/alerts", base_url=ONLINE).get_json()["state"] == "local"


def test_rate_limit_on_adds_and_tests(rt):
    got = rt.add().get_json()
    path = f"/api/alerts/phones/{got['id']}/test"
    for _ in range(alerts.PUSH_LIMIT - 1):   # the add above used one
        assert rt.call("post", path, {"secret": got["secret"]}).status_code == 202
    r = rt.call("post", path, {"secret": got["secret"]})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert rt.add({**SUB, "endpoint": "https://fcm.googleapis.com/fcm/send/z"}).status_code == 429


# ---- baselines: what is due when a phone turns alerts on, or unmutes an item, is never sent

PM = {"id": "pm", "label": "PM2.5 outside", "num": "40", "unit": "µg/m³", "ctx": "167% over the 15 limit", "facts": {},
      "action": {"text": "Keep the windows shut", "when": "now", "when_end": "2026-11-20T15:00:00+00:00"}}


def test_subscribe_while_co2_is_due_sends_only_later_actions(tmp_path, monkeypatch):
    rt = Routes(tmp_path, monkeypatch)
    f = Fake(tmp_path, store=rt.store)
    assert rt.add().status_code == 201
    assert f.checker.check_once(T0) == 0 and f.pushed == []   # the baseline: CO2 is recorded, not sent
    assert "co2" in rt.store.phones()[0].sent and rt.store.phones()[0].baseline == frozenset()
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 0
    f.items = [CO2, PM]   # a new action
    assert f.checker.check_once(T0 + timedelta(minutes=4)) == 1
    assert f.pushed == [(SUB["endpoint"], "PM2.5 is high outside")]


def test_unmute_while_co2_is_due_sends_nothing(tmp_path, monkeypatch):
    """CO2 was sent two days ago and left the Summary; it shows again while muted; the phone unmutes it."""
    rt = Routes(tmp_path, monkeypatch)
    f = Fake(tmp_path, store=rt.store)
    got = rt.add().get_json()
    path = f"/api/alerts/phones/{got['id']}"
    rt.call("patch", path, {"secret": got["secret"], "muted": ["co2"]})
    f.checker.check_once(T0)
    old = T0 - timedelta(days=2)
    rt.store.save_sent([Phone(id=got["id"], muted=frozenset(), quiet=(22, 7), since=old, sent={"co2": Sent(old, old)})])
    assert rt.call("patch", path, {"secret": got["secret"], "muted": []}).status_code == 204
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 0
    assert f.checker.check_once(T0 + timedelta(minutes=4)) == 0 and f.pushed == []


def test_unmute_during_a_check_waits_for_the_next(tmp_path, monkeypatch):
    """The owner unmutes CO2 while a check reads the Summary: that check doesn't send it, the next one records it."""
    rt = Routes(tmp_path, monkeypatch)
    f = Fake(tmp_path, store=rt.store)
    got = rt.add().get_json()
    path = f"/api/alerts/phones/{got['id']}"
    rt.call("patch", path, {"secret": got["secret"], "muted": ["co2"]})
    f.items = []
    f.checker.check_once(T0)   # the baseline, with nothing due
    f.items = [CO2]
    real = f.fetch
    def unmute_meanwhile(path_, body=None):
        if path_ == "/api/current":
            rt.call("patch", path, {"secret": got["secret"], "muted": []})
        return real(path_, body)
    f.checker._fetch = unmute_meanwhile
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 0   # the baselines were taken before the unmute: CO2 waits
    f.checker._fetch = real
    assert f.checker.check_once(T0 + timedelta(minutes=4)) == 0 and f.pushed == []
    assert rt.store.phones()[0].baseline == frozenset() and "co2" in rt.store.phones()[0].sent


def test_failed_read_keeps_the_baseline_waiting(tmp_path):
    f = Fake(tmp_path)
    f.phone(baseline=True)
    f.current = {"error": "AirGradient answered HTTP 502"}
    assert f.checker.check_once(T0) == 0
    assert f.store.phones()[0].baseline == {alert_rules.BASELINE_ALL}
    f.current = None
    assert f.checker.check_once(T0 + timedelta(minutes=2)) == 0 and f.pushed == []   # the baseline, at last
    assert f.store.phones()[0].baseline == frozenset()


# ---- an Offline monitor: its alerts wait

def test_offline_indoor_sends_only_the_offline_notice_through_the_real_path(c, tmp_path):
    """The humid moment read two hours later, through the app's own routes: the indoor monitor is Offline, so
    "Run the dehumidifier" waits; only the Offline notices go out."""
    moment = MOMENTS["humid"]
    def fetch(path, body=None):
        path += ("&" if "?" in path else "?") + "moment=humid"
        r = c.post(path, json=body) if body is not None else c.get(path)
        return r.get_json()
    page = read(c, "humid")["summary"]["items"]
    assert any(i["id"] == "rh" for i in page)   # the page still lists it from the last reading
    f = Fake(tmp_path)
    f.checker._fetch = fetch
    pid, secret = f.store.add_phone({"endpoint": "https://push.example/a", "keys": {}}, "Phone", moment.at - timedelta(days=1))
    f.store.set_choices(pid, secret, set(), (0, 0))   # no quiet hours: two hours after 8:30 PM is 10:30 PM
    f.store.take_baselines(lambda phone: None)
    later = moment.at + timedelta(hours=2)
    items, offline, _ = alerts.read_items(fetch, later)
    assert "indoor" in offline and not any(i["id"] in ("co2", "rh") for i in items)
    assert {i["id"] for i in items} <= {i["id"] for i in page}   # an alert shows a part of the page, never something else
    assert f.checker.check_once(later) == 1
    (_, title), = f.pushed
    assert title == ("Both monitors are offline" if "outdoor" in offline else "Indoor monitor is offline")
    assert "dehumidifier" not in title.lower()


def test_reading_source_gate(monkeypatch):
    """Alerts run where Home Air reads the home: a token, or the monitors read on the Wi-Fi without one."""
    import server
    for mode, poller, expected in (("live", None, True), ("local", object(), True), ("local", None, False),
                                   ("no_token", None, False), ("demo", object(), False)):
        monkeypatch.setattr(server, "MODE", mode)
        monkeypatch.setattr(server, "POLLER", poller)
        assert server.reads_home() is expected, (mode, poller)


# ---- removing a phone from the Phones list: no secret, one notice, then the row goes

def two_phones(rt):
    mine = rt.add(**IPHONE).get_json()
    other = rt.add({**SUB, "endpoint": "https://web.push.apple.com/other"}, **IPHONE).get_json()
    rt.pushed.clear()
    return mine, other


def test_remove_another_phone_sends_one_notice(rt):
    mine, other = two_phones(rt)
    r = rt.call("delete", f"/api/alerts/phones/{other['id']}", {})
    assert r.status_code == 204 and r.headers["Cache-Control"] == "no-store"
    assert rt.pushed == [("https://web.push.apple.com/other", alerts.REMOVED_TITLE, alerts.REMOVED_BODY)]
    assert [p["id"] for p in rt.store.listing()] == [mine["id"]]
    assert rt.call("delete", f"/api/alerts/phones/{other['id']}", {}).status_code == 404   # already gone: no second notice
    assert len(rt.pushed) == 1


@pytest.mark.parametrize("result", ["gone", "error", "raise", "raise KeyError"])
def test_remove_another_phone_whatever_the_push_answers(tmp_path, monkeypatch, result):
    def send(*a, **k):
        if result == "raise":
            raise RuntimeError("push service down")
        if result == "raise KeyError":   # a sender's own KeyError is not "removed meanwhile"
            raise KeyError("keys")
        return result
    r = Routes(tmp_path, monkeypatch)
    app = Flask(__name__)
    alerts.init(app, lambda: r.store, tz=NY, clock=lambda: r.now, send=send)
    r.c = app.test_client()
    r.store.add_phone(SUB, "iPhone", T0)
    pid = r.store.listing()[0]["id"]
    assert r.call("delete", f"/api/alerts/phones/{pid}", {}).status_code == 204 and r.store.listing() == []


def test_two_removals_at_once_send_one_notice(rt):
    rt.store.add_phone(SUB, "iPhone", T0)
    pid = rt.store.listing()[0]["id"]
    assert rt.store.remove_if_present(pid) and not rt.store.remove_if_present(pid)
    rt.store.add_phone(SUB, "iPhone", T0)
    pid = rt.store.listing()[0]["id"]
    before = len(rt.pushed)
    assert rt.call("delete", f"/api/alerts/phones/{pid}", {}).status_code == 204
    assert rt.call("delete", f"/api/alerts/phones/{pid}", {}).status_code in (204, 404)
    assert len(rt.pushed) == before + 1


def test_remove_another_phone_keeps_the_guards(rt, tmp_path, monkeypatch):
    mine, other = two_phones(rt)
    path = f"/api/alerts/phones/{other['id']}"
    assert rt.call("delete", path, {}, Origin="https://evil.example").status_code == 403
    assert rt.call("delete", path, {}, base="http://localhost:5555").status_code == 403
    assert rt.call("delete", "/api/alerts/phones/nobody", {}).status_code == 404
    assert rt.call("delete", path, {"secret": mine["secret"]}).status_code == 403   # another phone's secret is no key
    assert len(rt.store.listing()) == 2 and rt.pushed == []
    demo = Routes(tmp_path / "d", monkeypatch, demo=True)
    demo.store.add_phone(SUB, "iPhone", T0)
    assert demo.call("delete", f"/api/alerts/phones/{demo.store.listing()[0]['id']}", {}).status_code == 403
    assert len(demo.store.listing()) == 1


def test_remove_another_phone_counts_in_the_push_limit(rt):
    mine, other = two_phones(rt)
    path = f"/api/alerts/phones/{mine['id']}/test"
    for _ in range(alerts.PUSH_LIMIT - 2):   # the two adds used two
        assert rt.call("post", path, {"secret": mine["secret"]}).status_code == 202
    r = rt.call("delete", f"/api/alerts/phones/{other['id']}", {})
    assert r.status_code == 429 and len(rt.store.listing()) == 2
    assert not any(t == alerts.REMOVED_TITLE for _, t, _ in rt.pushed)
    assert rt.call("delete", f"/api/alerts/phones/{mine['id']}", {"secret": mine["secret"]}).status_code == 204   # turning itself off is never limited


def test_fresh_readings_only_from_a_fresh_indoor_row():
    """The margin rule never counts an old number: a stale row, an Offline monitor or a row over READING_FRESH old
    gives nothing."""
    now = T0
    row = {"locationType": "indoor", "timestamp": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
           "rco2_corrected": 650, "rhum": 57}
    assert alerts._fresh_readings([row], {}, now) == {"co2": 650, "rh": 57, "at": now - timedelta(minutes=5)}
    assert alerts._fresh_readings([{**row, "stale": True}], {}, now) == {}
    assert alerts._fresh_readings([row], {"indoor": now}, now) == {}
    old = {**row, "timestamp": (now - alerts.READING_FRESH - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")}
    assert alerts._fresh_readings([old], {}, now) == {}
    assert alerts._fresh_readings([{**row, "source": "public"}], {}, now) == {}
