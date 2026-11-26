"""The rules for phone alerts: which of the Summary's actions are due, for which phone, and what the notification says.
No network, no clock of its own: the caller passes the Summary's items, `now` and the home's time zone."""
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from summary import LIMITS, NIGHT, clock, fmt_range

WINDOW_LEAD = timedelta(hours=2)       # a window action ("8–11 AM") is due this long before it starts, to air the room first
REPEAT_HOLD = timedelta(hours=1)       # an action that left the Summary isn't sent again to that phone within this
OFFLINE_AFTER = timedelta(minutes=60)  # a monitor with no reading for longer than this is Offline (static/home.js)
QUIET_DEFAULT = NIGHT                  # hours (start, end) with no alerts, the hours the app calls "last night"
OFFLINE_PREFIX = "offline:"            # the key of an Offline send: "offline:indoor", "offline:outdoor"
OFFLINE_ITEM = "offline"               # the id a phone mutes to stop Offline alerts
MONITOR_NAMES = {"indoor": "Indoor monitor", "outdoor": "Outdoor monitor"}
AIRING_OK = {"when CO₂ is high": "to lower CO₂", "to dry the room": "to dry the room"}   # summary.AIRING_WHEN -> the pollen alert's words
ALERT_NAMES = {"co2": "CO₂", "rh": "humidity", "pm": "PM2.5"}   # what an Offline alert says is paused, per item id
NEEDS_MONITOR = {"co2": "indoor", "rh": "indoor", "pm": "outdoor"}   # the own monitor each item's reading comes from; pollen needs none
REARM_CO2 = 100                        # ppm under the CO₂ limit the room must reach before a CO₂ action is sent again
REARM_RH = 2                           # humidity points inside the limit (under damp, over dry) before a humidity action is sent again
REARMED = ("co2", "rh", "rh:dry")      # the action keys that wait for the reading to be clearly better before they are sent again
CHECK_GAP = timedelta(minutes=2)       # the time between two checks (alerts.ALERT_CHECK_SECONDS): a reading taken since the last check that listed the action counts
BASELINE_ALL = "*"                     # in a phone's `baseline`: every item and Offline (a phone that just turned alerts on)


@dataclass
class Sent:
    at: datetime
    left: datetime | None = None   # when it left the Summary; None while it is still listed
    clear: bool = False            # the reading has been clearly better since it left (REARMED keys only)


@dataclass
class Phone:
    id: str
    muted: frozenset[str]
    quiet: tuple[int, int]
    since: datetime
    sent: dict[str, Sent] = field(default_factory=dict)
    baseline: frozenset[str] = frozenset()   # item ids (or BASELINE_ALL) whose actions due now the next check records unsent


@dataclass
class Send:
    phone_id: str
    keys: list[str]
    title: str
    body: str


def action_key(item):
    """One action: the item's id; a new dirty stretch of PM2.5 (another end) is a new one; a dry room is another
    action than a damp one ("rh:dry"), so a switch between them never waits on the other's margin."""
    if item["id"] == "pm":
        return f"pm:{item['action']['when_end']}"
    if item["id"] == "rh" and (item.get("facts") or {}).get("dry"):
        return "rh:dry"
    return item["id"]


def clearly_better(key, readings):
    """True when this check's fresh reading is clearly on the good side of the action's limit (REARM_*): CO₂ under the
    limit minus REARM_CO2, a damp room under its limit minus REARM_RH, a dry room over its limit plus REARM_RH. No
    reading (None, stale, Offline): False, so nothing changes."""
    value = (readings or {}).get("co2" if key == "co2" else "rh")
    if value is None:
        return False
    if key == "co2":
        return value < LIMITS["co2"] - REARM_CO2
    if key == "rh":
        return value < LIMITS["rh_damp"] - REARM_RH
    if key == "rh:dry":
        return value > LIMITS["rh_dry"] + REARM_RH
    return False


def _on(day, clock_text, tz, meridian=None):
    """'4:30 PM' (or '4:30' with the meridian of its range) on `day`, or None."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))? ?(AM|PM)?", clock_text)
    mer = (m.group(3) if m else None) or meridian
    if not m or not mer or not 1 <= int(m.group(1)) <= 12:
        return None
    return datetime.combine(day, time(int(m.group(1)) % 12 + (12 if mer == "PM" else 0), int(m.group(2) or 0)), tz)


def _start_of(item_action, now, tz):
    """The start of 'after 11 AM' or '10 AM–1 PM', else None. A form that doesn't read back as clock() / fmt_range()
    writes it isn't recognised. The action's `when_end` (the end of the stretch, ISO) says which day; without it, the
    time is on the home's date today, and a window that spans midnight is on the day before while it still runs."""
    when = item_action.get("when") or ""
    end = item_action.get("when_end")
    end = datetime.fromisoformat(end).astimezone(tz) if end else None
    today = now.astimezone(tz).date()
    if when.startswith("after "):
        if end and clock(end) == when[6:]:
            return end
        t = _on(today, when[6:], tz)
        return t if t and clock(t) == when[6:] else None
    if "–" not in when:
        return None
    a_text, b_text = when.split("–", 1)
    days = [end.date() - timedelta(days=1), end.date()] if end else [today - timedelta(days=1), today]
    for day in days:
        t = _on(day, a_text, tz, meridian=b_text[-2:])
        b = _on(day, b_text, tz)
        if not (t and b):
            continue
        b = b if b > t else b + timedelta(days=1)
        if fmt_range(t, b) != when or (end and b != end) or (not end and day != today and b <= now):
            continue
        return t
    return None


def due_at(item, now, tz):
    """When the item's action is due: now, except 'after 11 AM' (at 11 AM) and a window (WINDOW_LEAD before it starts).
    None when the item has no action text."""
    act = item.get("action") or {}
    if not act.get("text"):
        return None
    when = act.get("when") or ""
    start = _start_of(act, now, tz)
    if start is None:
        return now
    return start - WINDOW_LEAD if "–" in when else start


def in_quiet(t, quiet, tz):
    start, end = quiet
    h = t.astimezone(tz).hour
    if start == end:
        return False
    return start <= h or h < end if start > end else start <= h < end


def quiet_end(t, quiet, tz):
    """The next time quiet hours end after `t` (home time)."""
    local = t.astimezone(tz)
    end = datetime.combine(local.date(), time(quiet[1]), tz)
    return end if end > t else datetime.combine(local.date() + timedelta(days=1), time(quiet[1]), tz)


def _limit(item):
    """'over the 800 limit', 'under the 30% limit': the home's limit on the item's side."""
    if item["id"] == "rh":
        side, limit = ("under", LIMITS["rh_dry"]) if (item.get("facts") or {}).get("dry") else ("over", LIMITS["rh_damp"])
    elif item["id"] in ("co2", "pm"):
        side, limit = "over", LIMITS[item["id"]]
    else:
        return ""
    return f"{side} the {limit}{'%' if item['unit'] == '%' else ''} limit"


def _when_now(act, now, tz):
    """The action's time as a notification arriving at `now` says it: nothing for "now" or for "after 11 AM" once it is
    11 AM (the arrival is the time), "before the 8 AM rush" for "now, before the 8 AM rush", else the Summary's words."""
    when = act.get("when") or ""
    if when == "now":
        return ""
    if when.startswith("now, "):
        return when[5:]
    if now is not None and when.startswith("after "):
        start = _start_of(act, now, tz or now.tzinfo)
        if start is not None and start <= now:
            return ""
    return when


def situation(item):
    """What the reading is doing, in a few words: the notification's title ("CO₂ is high inside"). The setting that
    mutes it uses the same subject (static/alerts.js ITEMS)."""
    facts, when = item.get("facts") or {}, item["action"].get("when") or ""
    if item["id"] == "co2":
        return "CO₂ is high inside"
    if item["id"] == "rh":
        return "The air inside is too dry" if facts.get("dry") else "Humidity is high inside"
    if item["id"] == "pm":
        cause = facts.get("cause") or ""
        what = ("Smoke outside" if cause == "smoke" else "Traffic exhaust outside" if cause.endswith(" rush")
                else "PM2.5 is high outside")
        return what + (f" {when}" if "–" in when else "")   # a window still ahead: "Traffic exhaust outside 8–11 AM"
    if item["id"] == "pollen":
        kind = facts.get("pollen_type")
        return f"{str(kind).capitalize()} pollen is high today" if kind else "Pollen is high today"
    return item["label"]


def _do(item, now, tz):
    """The action with its time as it reads on arrival, as a sentence without its full stop."""
    act = item["action"]
    when = _when_now(act, now, tz)
    if when == "drier outside":
        return f"{act['text']} while it’s drier outside"
    return f"{act['text']} {when}" if when else act["text"]


def _number(item):
    """The reading and the limit, nothing else: '1,250 ppm, over the 800 limit'. The page has the reasons."""
    value = item["num"] + ("%" if item["unit"] == "%" else f" {item['unit']}")
    if item["id"] == "pm":
        value = ("PM2.5 up to " if item.get("forecast") else "PM2.5 ") + value
    return f"{value}, {_limit(item)}"


def notification_text(item, now=None, tz=None):
    """(title, body): what the reading is doing; then what to do and when, and the reading against its limit."""
    facts = item.get("facts") or {}
    if item["id"] == "pollen":   # the index is in the title's "high"; an airing beside it is the one exception
        return situation(item), f"{_do(item, now, tz)}." + (f" You can open a window briefly {AIRING_OK[facts['airing_when']]}."
                                                          if facts.get("airing_when") in AIRING_OK else "")
    return situation(item), f"{_do(item, now, tz)} ({_number(item)})."   # the number after the action


def also_text(item, now=None, tz=None):
    """The item after "Also:" in a notification that carries several: its action and time ("run the dehumidifier until
    under 55%")."""
    do = _do(item, now, tz)
    return do[0].lower() + do[1:]


def _offline_text(down, tz):
    """(title, body) of an Offline alert for the monitors in `down` (kind -> last reading), one or both."""
    kinds = [k for k in MONITOR_NAMES if k in down]
    paused = [ALERT_NAMES[i] for i, k in NEEDS_MONITOR.items() if k in kinds]
    names = (", ".join(paused[:-1]) + " and " if len(paused) > 1 else "") + paused[-1]
    if len(kinds) == 1:
        since = clock(down[kinds[0]].astimezone(tz))
        return (f"{MONITOR_NAMES[kinds[0]]} is offline",
                f"No reading since {since}. Check its power and Wi-Fi. {names} alerts are paused.")
    since = clock(max(down.values()).astimezone(tz))   # the later one: neither has read since
    return ("Both monitors are offline",
            f"No reading since {since}. Check their power and the Wi-Fi. {names} alerts are paused.")


def _unsent(entry, now, key=None):
    """True when nothing was sent for this key, or it left the Summary more than REPEAT_HOLD ago (and, for a REARMED
    key, its reading has been clearly better since)."""
    if entry is None:
        return True
    return entry.left is not None and now - entry.left >= REPEAT_HOLD and (key not in REARMED or entry.clear)


def without_offline(items, offline):
    """The items whose own monitor is not Offline: an action read from a monitor that stopped sending is not sent.
    `offline` names only the home's own monitors, so a PM2.5 item from a public monitor or the model stays."""
    return [i for i in items if NEEDS_MONITOR.get(i["id"]) not in offline]


def plan_sends(items, offline, phones, now, tz):
    """What to send now: one Send per phone with everything due at this check, Offline included. The title and body are
    the first one's (actions in Summary order, then Offline monitors); each other one follows "Also:" as its subject and
    action (`also_text`), or "indoor monitor is offline" (both monitors in one). `offline` maps each monitor kind with no reading for OFFLINE_AFTER to its last
    reading time. An item waiting for its baseline (`phone.baseline`) is not sent. The caller records each sent key in
    `phone.sent` and calls `update_sent` after."""
    sends = []
    for phone in phones:
        if in_quiet(now, phone.quiet, tz) or BASELINE_ALL in phone.baseline:
            continue
        due = []   # (keys, (title, body), its words after "Also:") in the order they are told
        for item in items:
            when = due_at(item, now, tz)
            if (when is None or when > now or when <= phone.since or item["id"] in phone.muted | phone.baseline
                    or not _unsent(phone.sent.get(action_key(item)), now, action_key(item))):
                continue
            due.append(([action_key(item)], notification_text(item, now, tz), also_text(item, now, tz)))
        if OFFLINE_ITEM not in phone.muted | phone.baseline:
            down = {k: last for k, last in offline.items() if _unsent(phone.sent.get(OFFLINE_PREFIX + k), now, OFFLINE_PREFIX + k)}
            if down:   # both monitors in one line: a power or Wi-Fi cut is one event
                text = _offline_text(down, tz)
                due.append(([OFFLINE_PREFIX + k for k in MONITOR_NAMES if k in down], text, text[0][0].lower() + text[0][1:]))
        if due:
            title, body = due[0][1]
            if due[1:]:
                body += " Also: " + "; ".join(also for _, _, also in due[1:]) + "."
            sends.append(Send(phone.id, [k for keys, _, _ in due for k in keys], title, body))
    return sends


def update_sent(phones, items, now, offline=(), readings=None):
    """Mark `left` on sent actions (and Offline alerts) no longer on the Summary; clear it when one comes back within
    REPEAT_HOLD. One that stayed away longer keeps its `left`, so the next plan_sends sends it again (a REARMED one once
    `clear`: this check's fresh `readings`, {"co2": ppm, "rh": %, "at": when measured}, taken after it left, were
    clearly better). Every phone's
    entries, whatever its quiet hours or mutes: a drop seen at night still counts."""
    listed = {action_key(i) for i in items if (i.get("action") or {}).get("text")} | {OFFLINE_PREFIX + k for k in offline}
    for phone in phones:
        for key, sent in phone.sent.items():
            if key not in listed:
                sent.left = sent.left or now
                taken = (readings or {}).get("at", now)   # a reading from before the last check that listed it proves nothing
                if key in REARMED and taken > sent.left - CHECK_GAP and clearly_better(key, readings):
                    sent.clear = True
            elif sent.left is not None and now - sent.left < REPEAT_HOLD:
                sent.left, sent.clear = None, False   # back within the hold: the same episode, which must clear again


def seed(phone, items, now, tz, offline=()):
    """Record every action due now, and each current outage, in `phone.sent` without sending: they never go out to this
    phone, so only actions that become due later are sent. An entry that had left the Summary is replaced (`_record`). The checker calls it, through take_baseline, for a phone that just turned
    alerts on or unmuted an item."""
    for item in items:
        when = due_at(item, now, tz)
        if when is not None and when <= now:
            _record(phone, action_key(item), now)
    for kind in offline:
        _record(phone, OFFLINE_PREFIX + kind, now)


def take_baseline(phone, items, now, tz, offline=()):
    """Seed the items in `phone.baseline` (all of them for BASELINE_ALL) from this check's read, without sending."""
    if BASELINE_ALL not in phone.baseline:
        items = [i for i in items if i["id"] in phone.baseline]
        offline = offline if OFFLINE_ITEM in phone.baseline else {}
    seed(phone, items, now, tz, offline=offline)


def _record(phone, key, now):
    """A baseline: the action due now is this phone's current episode, recorded unsent. An entry still on the Summary
    stays; one that had left (any hold, any margin) is replaced, so an unmuted item starts fresh."""
    entry = phone.sent.get(key)
    if entry is None or entry.left is not None:
        phone.sent[key] = Sent(now)
