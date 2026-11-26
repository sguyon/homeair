"""The alert check on a timer: every ALERT_CHECK_SECONDS it reads the same Summary the page shows, asks alert_rules what
is due for each phone, and sends it through web push. It runs where Home Air reads the home (a token, or the monitors
on the Wi-Fi) and has push keys (server.py); phones can be added only on Home Air online (available)."""
import ipaddress
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

import agent_tools
import alert_push
import alert_rules
import alert_store
from alert_rules import OFFLINE_AFTER, Sent
from config import env_key

log = logging.getLogger("aq.alerts")

ALERT_CHECK_SECONDS = 120  # seconds between checks: an action is due within two minutes, and each check reads from the sources' caches.
READING_FRESH = timedelta(minutes=15)   # an indoor reading older than this never counts as "clearly better" (cloud rows come every 5 min)


def push_keys_set():
    return bool(alert_push.public_key() and env_key("VAPID_PRIVATE_KEY", os.environ))


def _private_host(host):
    """localhost, a .local name, or a loopback or private LAN address: a phone can't be pushed to from there."""
    name = host.rpartition(":")[0] if host.count(":") == 1 else host   # drop the port; an IPv6 literal keeps its colons
    name = name.strip("[]").lower()
    if name == "localhost" or name.endswith(".local") or name.endswith(".localhost"):
        return True
    if host.startswith("[") and "]" in host:
        name = host[1:host.index("]")]
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def available(request, demo=False, reading=True):
    """"online" (HTTPS on a public address, push keys set: phones can be added), "local" (this computer or the home
    network: the page says to put Home Air online), "setup" (HTTPS on a public address without push keys: the page says
    alerts aren't set up) or "demo". X-Forwarded-Proto is trusted for the HTTPS part only: a
    forged header can't make a private address online, and it adds no phone by itself. Home Air with no reading source
    (`reading` False: no token and no monitor read on the Wi-Fi, so no checker runs) is "local" too: a phone added
    there would never get an alert."""
    if demo:
        return "demo"
    if not reading:
        return "local"
    secure = request.is_secure or request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https"
    if secure and not _private_host(request.host):
        return "online" if push_keys_set() else "setup"
    return "local"


def _offline(rows, now):
    """Each own monitor with no reading for OFFLINE_AFTER, mapped to its last reading time. A monitor with no row or
    no time is no information, not Offline; a public monitor standing in for outdoor isn't the home's to check."""
    out = {}
    for kind in ("indoor", "outdoor"):
        row = next((r for r in rows if isinstance(r, dict) and r.get("locationType") == kind and not r.get("source")), None)
        try:
            last = agent_tools._utc(row["timestamp"]) if row else None
        except (KeyError, ValueError):
            last = None
        if last is not None and now - last > OFFLINE_AFTER:
            out[kind] = last
    return out


def fetch_in_process(app, path, body=None):
    """The app's own JSON for one of its routes, in process: the same code, caches and budgets as the page."""
    with app.app_context(), app.test_client() as c:
        r = c.post(path, json=body) if body is not None else c.get(path)
    try:
        return json.loads(r.get_data(as_text=True))
    except ValueError:
        return {"error": f"{path.split('?')[0]} answered HTTP {r.status_code}"}


def _fresh_readings(rows, offline, now):
    """{"co2": ppm, "rh": %, "at": when measured} from the indoor monitor's row when it is fresh (not stale, not Offline, under
    READING_FRESH old), else {}: the margin rule (alert_rules.clearly_better) never counts an old number."""
    row = next((r for r in rows if isinstance(r, dict) and r.get("locationType") == "indoor" and not r.get("source")), None)
    if not row or row.get("stale") or "indoor" in offline:
        return {}
    try:
        taken = agent_tools._utc(row["timestamp"])
    except (KeyError, ValueError):
        return {}
    if now - taken > READING_FRESH:
        return {}
    got = agent_tools._reading(row, agent_tools.READING_KEYS) or {}
    out = {k: v for k, v in (("co2", got.get("co2")), ("rh", got.get("humidity"))) if isinstance(v, (int, float))}
    return {**out, "at": taken} if out else {}


def read_items(fetch, now):
    """(the Summary's items, the Offline monitors, the indoor monitor's fresh readings) as the page reads them, or None
    when no monitor answered or a read failed: no information, so nothing is sent or marked Offline. The items read
    from an Offline monitor are left out: the Offline alert says the alerts that need it wait, so they do."""
    try:
        got = agent_tools.read_now(fetch)
    except agent_tools.ToolError as e:
        log.info("alert read skipped: %s", e)
        return None
    rows = got["rows"] if isinstance(got["rows"], list) else []
    if not rows:
        return None
    offline = _offline(rows, now)
    items = alert_rules.without_offline((got["read"].get("summary") or {}).get("items") or [], offline)
    return items, offline, _fresh_readings(rows, offline, now)


class Checker:
    def __init__(self, app, store, send=alert_push.send, clock=lambda: datetime.now(timezone.utc), tz=timezone.utc):
        self.app, self.store, self.send, self.clock, self.tz = app, store, send, clock, tz
        self._started = False

    def _fetch(self, path, body=None):
        return fetch_in_process(self.app, path, body)

    def check_once(self, now):
        """One check: the number of alerts sent. A read that fails sends nothing, marks nothing Offline and takes no
        baseline. A phone waiting for its baseline (just added, or an item just unmuted) gets it here, unsent."""
        phones = self.store.phones()
        if not phones:
            return 0
        got = read_items(self._fetch, now)
        if got is None:   # no information: no alert and no Offline
            return 0
        items, offline, readings = got
        self.store.take_baselines(lambda phone: alert_rules.take_baseline(phone, items, now, self.tz, offline))
        # again, after the read and the baselines: a phone turned on or an item unmuted since then waits for the next check
        phones = self.store.phones()
        sent = 0
        by_id = {p.id: p for p in phones}
        before = {p.id: {k: (v.at, v.left, v.clear) for k, v in p.sent.items()} for p in phones}
        for s in alert_rules.plan_sends(items, offline, phones, now, self.tz):
            phone = by_id.get(s.phone_id)
            if phone is None:   # removed earlier in this check
                continue
            try:
                subscription = self.store.subscription(s.phone_id)
            except KeyError:   # turned off on the page since the phones were read
                by_id.pop(s.phone_id)
                continue
            result = self.send(subscription, s.title, s.body)
            if result == "ok":
                sent += 1
                phone.sent.update({k: Sent(now) for k in s.keys})
                self.store.save_sent([replace(phone, sent={k: phone.sent[k] for k in s.keys})])   # at once, only these keys: a later failure can't make this send go out again
            elif result == "gone":
                self.store.remove(s.phone_id)   # the push service dropped this phone: no secret, no web request
                by_id.pop(s.phone_id, None)
            # "error": nothing is recorded, so the next check tries again
        phones = list(by_id.values())
        alert_rules.update_sent(phones, items, now, offline, readings)
        # only the keys this check changed: a key written meanwhile (a phone turned on, an item unmuted) stays as it was written
        self.store.save_sent([replace(p, sent={k: v for k, v in p.sent.items() if before[p.id].get(k) != (v.at, v.left, v.clear)})
                              for p in phones])
        return sent

    def start(self):
        """A daemon thread that checks every ALERT_CHECK_SECONDS and never raises out of the loop. Once only."""
        if self._started:
            return
        self._started = True

        def loop():
            while True:
                try:
                    self.check_once(self.clock())
                except Exception:   # the loop outlives any one failure
                    log.exception("alert check failed")
                time.sleep(ALERT_CHECK_SECONDS)
        threading.Thread(target=loop, name="alerts", daemon=True).start()


# ---- the routes: turn alerts on, choose, test, turn off, remove a phone ----

MAX_BODY = 4096          # bytes: a push subscription is under 1 KB, the choices are smaller
MAX_ENDPOINT = 1024      # characters in a subscription's address
MAX_KEY = 256            # characters in each of a subscription's two keys
MAX_MUTED = 20           # items a phone can mute: the Summary's items, plus Offline
MAX_ITEM_ID = 40         # characters in an item's id
PUSH_LIMIT = 20          # test notifications and new phones, together, per PUSH_WINDOW_S: each makes the server call a push service
PUSH_WINDOW_S = 60       # seconds
TEST_TITLE = "Test: alerts work on this phone"   # "phone", never the model
TEST_BODY = "Home Air sends each action here when it is due.{quiet}"   # quiet: " No alerts from 10 PM to 7 AM." (the phone's own hours), or nothing
REMOVED_TITLE = "Alerts are off on this phone"   # the one notice a phone removed from the Phones list gets
REMOVED_BODY = "They were turned off from another phone or computer. To get them again, open Home Air, tap the bell and turn on Allow Alerts."
DEVICES = (("iPhone", "iPhone"), ("Android", "Android phone"))   # a User-Agent word and the name kept; anything else is "Phone"
DEVICE_OTHER = "Phone"


def device_name(user_agent):
    """A name for the page's Phones list; the User-Agent itself is never kept."""
    for word, name in DEVICES:
        if word in (user_agent or ""):
            return name
    return DEVICE_OTHER


def _hour_text(hour):
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def _test_text(quiet=alert_rules.QUIET_DEFAULT):
    except_ = f" No alerts from {_hour_text(quiet[0])} to {_hour_text(quiet[1])}." if quiet[0] != quiet[1] else ""
    return TEST_TITLE, TEST_BODY.format(quiet=except_)


def _scheme():
    forwarded = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    return "https" if request.is_secure or forwarded == "https" else "http"


def _same_origin():
    """The Origin header, when the browser sends one, must be this page's own scheme and host. This is also what stops
    DNS rebinding: a page on another site that points its name at this server sends its own name as Origin; and the
    scheme side rests on available(), which is "online" only for https."""
    origin = request.headers.get("Origin")
    return origin is None or origin == f"{_scheme()}://{request.host}"


def _valid_subscription(sub):
    if not isinstance(sub, dict) or not isinstance(sub.get("endpoint"), str) or not isinstance(sub.get("keys"), dict):
        return False
    keys = sub["keys"]
    if not all(isinstance(keys.get(k), str) and 0 < len(keys[k]) <= MAX_KEY for k in ("p256dh", "auth")):
        return False
    endpoint = sub["endpoint"]
    try:
        urlsplit(endpoint)
    except ValueError:
        return False
    return len(endpoint) <= MAX_ENDPOINT


def _valid_quiet(quiet):
    return (isinstance(quiet, list) and len(quiet) == 2
            and all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in quiet))


def _valid_muted(muted):
    return (isinstance(muted, list) and len(muted) <= MAX_MUTED
            and all(isinstance(m, str) and 0 < len(m) <= MAX_ITEM_ID for m in muted))


def make_blueprint(app, get_store, demo=lambda: False, reading=lambda: True, tz=timezone.utc, send=alert_push.send,
                   clock=lambda: datetime.now(timezone.utc)):
    """The routes. `get_store()` is the alert store, or None while there are no push keys; `reading()` says whether
    Home Air has a reading source (available)."""
    bp = Blueprint("alerts", __name__)
    pushes = deque()   # when each recent push route call was made (monotonic seconds)

    def reply(data=None, status=200):
        r = jsonify(data) if data is not None else app.response_class(status=status)
        if data is not None:
            r.status_code = status
        r.headers["Cache-Control"] = "no-store"
        return r

    def state():
        return available(request, demo=demo(), reading=reading())

    def body_or_refusal():
        """(the JSON body, None) or (None, the refusal): an online copy, a same-origin request, a small JSON object."""
        if state() != "online" or not _same_origin():
            return None, reply({"error": "refused"}, 403)
        if request.content_length is None:
            return None, reply({"error": "length required"}, 411)
        if request.content_length > MAX_BODY:
            return None, reply({"error": "too large"}, 413)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return None, reply({"error": "bad request"}, 400)
        return body, None

    def too_many():
        """A 429 once PUSH_LIMIT calls were made in PUSH_WINDOW_S (this copy as a whole: an address can be forged), else None."""
        now = time.monotonic()
        while pushes and pushes[0] <= now - PUSH_WINDOW_S:
            pushes.popleft()
        if len(pushes) >= PUSH_LIMIT:
            r = reply({"error": "too many"}, 429)
            r.headers["Retry-After"] = str(max(1, int(pushes[0] + PUSH_WINDOW_S - now) + 1))
            return r
        pushes.append(now)
        return None

    def check_secret(store, phone_id, body):
        secret = body.get("secret")
        if not isinstance(secret, str) or not secret:
            return reply({"error": "refused"}, 403)
        try:
            store.verify(phone_id, secret)
        except PermissionError:
            return reply({"error": "refused"}, 403)
        return None

    @bp.get("/api/alerts")
    def get_alerts():
        st = state()
        store = get_store()
        return reply({"state": st, "key": alert_push.public_key() if st == "online" else None,
                      "phones": store.listing() if store else [], "max": alert_store.MAX_PHONES})

    @bp.post("/api/alerts/phones")
    def add_phone():
        body, refused = body_or_refusal()
        if refused:
            return refused
        sub = body.get("subscription")
        if not _valid_subscription(sub):
            return reply({"error": "bad subscription"}, 400)
        if not alert_push.push_host_allowed(sub["endpoint"]):   # a browser whose push service isn't on the list: the page says so
            return reply({"error": "push service"}, 400)
        limited = too_many()
        if limited:
            return limited
        store = get_store()
        sub = {"endpoint": sub["endpoint"], "keys": {k: sub["keys"][k] for k in ("p256dh", "auth")}}
        device = device_name(request.headers.get("User-Agent"))
        now = clock().astimezone(tz)   # kept with the home's offset: the Phones list shows the home's date
        try:   # what is due now is recorded unsent by the next check (the phone's baseline), not here
            phone_id, secret = store.add_phone(sub, device, now)
        except alert_store.PhoneLimit:
            return reply({"error": "limit"}, 409)
        # a phone added again keeps its row: its device, the day it was added and its quiet hours
        row = next((d for d in store.listing() if d["id"] == phone_id), {"device": device, "since": now.isoformat()[:10]})
        result = send(sub, *_test_text(tuple(store.choices(phone_id, secret)["quiet"])))
        if result == "gone":   # the push service already refuses this address: no phone to keep
            store.remove(phone_id, secret)
            return reply({"sent": False, "gone": True}, 410)
        return reply({"id": phone_id, "secret": secret, "since": row["since"], "sent": result == "ok"}, 201)

    @bp.patch("/api/alerts/phones/<phone_id>")
    def choose(phone_id):
        body, refused = body_or_refusal()
        if refused:
            return refused
        store = get_store()
        refused = check_secret(store, phone_id, body)
        if refused:
            return refused
        before = next((p for p in store.phones() if p.id == phone_id), None)
        muted = body.get("muted", sorted(before.muted) if before else [])
        quiet = body.get("quiet", list(before.quiet) if before else list(alert_rules.QUIET_DEFAULT))
        if not _valid_muted(muted) or not _valid_quiet(quiet):
            return reply({"error": "bad choices"}, 400)
        try:   # an item turned back on waits for its baseline: what is due for it now is not sent, only what comes later
            store.set_choices(phone_id, body["secret"], set(muted), quiet)
        except PermissionError:
            return reply({"error": "refused"}, 403)
        return reply(status=204)

    @bp.post("/api/alerts/phones/<phone_id>/choices")
    def read_choices(phone_id):
        """This phone's choices, for its own panel: the page's copy in localStorage can be lost or out of date."""
        body, refused = body_or_refusal()
        if refused:
            return refused
        store = get_store()
        refused = check_secret(store, phone_id, body)
        if refused:
            return refused
        try:
            return reply(store.choices(phone_id, body["secret"]))
        except PermissionError:   # removed since the check
            return reply({"error": "refused"}, 403)

    @bp.delete("/api/alerts/phones/<phone_id>")
    def remove(phone_id):
        """With "secret": this phone turns its own alerts off (its page unsubscribes too). Without it: the Phones list
        removes a phone, from any phone or computer: the removed phone gets REMOVED_TITLE once, then its row goes. A failed notice doesn't
        stop the removal; the notice calls a push service, so it counts in the push limit."""
        body, refused = body_or_refusal()
        if refused:
            return refused
        store = get_store()
        if "secret" in body:
            refused = check_secret(store, phone_id, body)
            if refused:
                return refused
            try:
                store.remove(phone_id, body["secret"])
            except PermissionError:   # removed or changed since the check
                return reply({"error": "refused"}, 403)
            return reply(status=204)
        if not store.has(phone_id):
            return reply({"error": "not found"}, 404)
        limited = too_many()
        if limited:
            return limited
        try:
            sub = store.subscription(phone_id)
        except KeyError:   # removed meanwhile
            return reply(status=204)
        if not store.remove_if_present(phone_id):   # removed first: a second removal or the checker finds no phone
            return reply(status=204)
        try:
            send(sub, REMOVED_TITLE, REMOVED_BODY)   # "gone" or "error": removed all the same
        except Exception:   # the notice is a courtesy: never a reason to keep the phone (a KeyError from the sender too)
            log.exception("removal notice failed")
        return reply(status=204)

    @bp.post("/api/alerts/phones/<phone_id>/test")
    def test(phone_id):
        body, refused = body_or_refusal()
        if refused:
            return refused
        store = get_store()
        refused = check_secret(store, phone_id, body)
        if refused:
            return refused
        limited = too_many()
        if limited:
            return limited
        phone = next((p for p in store.phones() if p.id == phone_id), None)
        try:
            result = send(store.subscription(phone_id), *_test_text(phone.quiet if phone else alert_rules.QUIET_DEFAULT))
        except KeyError:   # removed since the check
            return reply({"error": "refused"}, 403)
        if result == "gone":
            store.remove(phone_id)
            return reply({"sent": False, "gone": True}, 410)
        return reply({"sent": result == "ok"}, 202 if result == "ok" else 502)

    return bp


def init(app, get_store, **kwargs):
    """Serve the alert routes on `app`."""
    app.register_blueprint(make_blueprint(app, get_store, **kwargs))
