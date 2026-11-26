"""The alert rules: what is due, for which phone, and what the notification says. No network. The demo moments' items
come from the demo's /api/analyze, so the fixtures stay the demo's; the other cases build small items by hand."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import summary
from alert_rules import (also_text, BASELINE_ALL, Phone, Sent, action_key, clearly_better, due_at, in_quiet, notification_text, plan_sends, quiet_end,
                         seed, take_baseline, update_sent, without_offline, OFFLINE_AFTER, QUIET_DEFAULT)
from demo.moments import MOMENTS
from test_demo import c, demo, read  # noqa: F401  (the demo app's fixtures)

NY = ZoneInfo("America/New_York")
LONG_AGO = datetime(2026, 1, 1, tzinfo=NY)


def item(id, text, when, label="CO₂ inside", num="1,240", unit="ppm", ctx="55% over the 800 limit", when_end=None, **extra):
    return {"id": id, "label": label, "num": num, "unit": unit, "ctx": ctx, "facts": {},
            "action": {"text": text, "when": when, "when_end": when_end}, **extra}


CO2 = item("co2", "Open a window for 10 min", "now")


def phone(**kw):
    return Phone(**{"id": "p1", "muted": frozenset(), "quiet": QUIET_DEFAULT, "since": LONG_AGO, "sent": {}, **kw})


def step(phones, items, now, offline=None, readings=None):
    """One check the way the caller runs it: plan, record what was sent, then mark what left."""
    offline = offline or {}
    sends = plan_sends(items, offline, phones, now, NY)
    for s in sends:
        for p in phones:
            if p.id == s.phone_id:
                p.sent.update({k: Sent(now) for k in s.keys})
    update_sent(phones, items, now, offline, readings)
    return sends


def moment_items(c, key):
    return read(c, key)["summary"]["items"], MOMENTS[key].at


def test_rush_moment_sends_pm_then_co2(c):
    items, at = moment_items(c, "rush")
    p = phone()
    sends = step([p], items, at)
    pm_end = next(i for i in items if i["id"] == "pm")["action"]["when_end"]
    assert len(sends) == 1 and sends[0].keys == [f"pm:{pm_end}"]   # the live sky's quieter rush: no damp item
    assert sends[0].title == "Traffic exhaust outside"
    assert sends[0].body == "Keep windows shut until 11 AM (PM2.5 up to 33 µg/m³, over the 15 limit)."
    later = step([p], items, at.replace(hour=11, minute=0))
    assert len(later) == 1 and later[0].keys == ["co2"]
    assert later[0].title == "CO₂ is high inside"   # it is 11 AM: "after 11 AM" would read as not yet
    assert later[0].body == "Open a window for 10 min (1,250 ppm, over the 800 limit)."


def test_a_road_action_alone_has_a_title_and_names_its_road():
    """A road's exhaust with PM2.5 under its limit is an action with no reading: the alert names the road instead of
    a number, and the checker doesn't fail on it."""
    traffic = item("traffic", "Keep the south-east windows shut", "until 11 AM", label="Traffic", num=None, unit=None,
                   ctx="the BQE")
    assert notification_text(traffic) == ("Traffic exhaust outside", "Keep the south-east windows shut until 11 AM (the BQE).")
    assert also_text(traffic) == "keep the south-east windows shut until 11 AM"


def test_clear_moment_sends_nothing(c):
    items, at = moment_items(c, "clear")
    assert step([phone()], items, at) == []


def test_humid_moment_sends_rh(c):
    items, at = moment_items(c, "humid")
    sends = step([phone()], items, at)
    assert [s.keys for s in sends] == [["rh"]]
    assert sends[0].title == "Humidity is high inside"
    assert sends[0].body == "Run the dehumidifier until under 55% (66%, over the 60% limit)."


def test_flapping_co2_sends_once():
    p, total = phone(), []
    t = datetime(2026, 11, 24, 9, 0, tzinfo=NY)
    for n in range(25):   # 2 hours, every 5 minutes
        total += step([p], [CO2] if n % 2 == 0 else [], t + timedelta(minutes=5 * n))
    assert len(total) == 1


def test_returns_after_the_hold_sends_again_once_clearly_better():
    p, t = phone(), datetime(2026, 11, 24, 9, 0, tzinfo=NY)
    assert len(step([p], [CO2], t)) == 1
    assert step([p], [], t + timedelta(minutes=10), readings={"co2": 650}) == []   # aired: 650, under 800 - 100
    assert step([p], [CO2], t + timedelta(minutes=80)) != []   # gone for over an hour, and the room was clearly better: a new need


def test_hovering_near_the_limit_is_one_episode():
    """The 7-day replay's Mon 23 Nov: humidity at 65% at 7 AM, under 60 for a while but never under 58, then 61% at
    4:40 PM: one alert, not two. CO₂ that dipped to 780 (not under 700) likewise."""
    damp = item("rh", "Run the dehumidifier", "until under 55%", label="Humidity inside", num="65", unit="%", facts={"rh": 65})
    p, t = phone(), datetime(2026, 11, 23, 7, 0, tzinfo=NY)
    assert len(step([p], [damp], t)) == 1
    for minutes in (120, 300, 500):
        assert step([p], [], t + timedelta(minutes=minutes), readings={"rh": 59}) == []
    assert step([p], [damp], t + timedelta(minutes=580), readings={"rh": 61}) == []
    q = phone()
    assert len(step([q], [CO2], t)) == 1
    step([q], [], t + timedelta(minutes=30), readings={"co2": 780})
    assert step([q], [CO2], t + timedelta(minutes=200)) == []
    step([q], [], t + timedelta(minutes=210), readings={"co2": 699})   # clearly better now; it left at 200
    assert step([q], [], t + timedelta(minutes=260)) == []
    assert step([q], [CO2], t + timedelta(minutes=280)) != []   # an hour after it left, and it was under 700


def test_a_reading_from_before_it_left_proves_nothing():
    """A cloud row can be minutes older than the local one that sent the alert: 650 measured before the action left
    the Summary doesn't clear it."""
    p, t = phone(), datetime(2026, 11, 24, 9, 0, tzinfo=NY)
    step([p], [CO2], t)
    step([p], [], t + timedelta(minutes=10), readings={"co2": 650, "at": t + timedelta(minutes=5)})
    assert not p.sent["co2"].clear
    step([p], [], t + timedelta(minutes=12), readings={"co2": 650, "at": t + timedelta(minutes=11)})
    assert p.sent["co2"].clear


def test_the_first_good_reading_counts_and_a_return_resets():
    """Codex, 26 Nov: a reading measured a minute before the check that sees the action leave still counts; an action
    back within the hold must clear again before a later return sends."""
    p, t = phone(), datetime(2026, 11, 24, 9, 0, tzinfo=NY)
    step([p], [CO2], t)
    step([p], [], t + timedelta(minutes=10), readings={"co2": 650, "at": t + timedelta(minutes=9)})
    assert p.sent["co2"].clear
    step([p], [CO2], t + timedelta(minutes=20))   # back within the hour: the same episode
    assert p.sent["co2"].left is None and not p.sent["co2"].clear
    step([p], [], t + timedelta(minutes=30), readings={"co2": 750, "at": t + timedelta(minutes=29)})
    assert step([p], [CO2], t + timedelta(minutes=100)) == []   # never under 700 since: no new alert


def test_no_reading_counts_as_nothing():
    p, t = phone(), datetime(2026, 11, 24, 9, 0, tzinfo=NY)
    step([p], [CO2], t)
    step([p], [], t + timedelta(minutes=10), readings={})   # stale or Offline: _fresh_readings gives nothing
    assert step([p], [CO2], t + timedelta(minutes=90)) == []


def test_a_dry_room_is_its_own_action():
    damp = item("rh", "Run the dehumidifier", "until under 55%", label="Humidity inside", num="65", unit="%", facts={"rh": 65})
    dry = item("rh", "Keep windows shut", "while it’s this dry outside", label="Humidity inside", num="25", unit="%", facts={"rh": 25, "dry": True})
    assert action_key(damp) == "rh" and action_key(dry) == "rh:dry"
    p, t = phone(), datetime(2026, 11, 24, 9, 0, tzinfo=NY)
    assert len(step([p], [damp], t)) == 1
    assert [s.keys for s in step([p], [dry], t + timedelta(minutes=10))] == [["rh:dry"]]   # never waits on the damp margin
    step([p], [], t + timedelta(minutes=20), readings={"rh": 33})   # over 30 + 2: the dry episode clears
    assert p.sent["rh:dry"].clear and p.sent["rh"].clear   # 33%: over 32 (dry clears) and under 58 (damp clears)
    step([p], [], t + timedelta(minutes=30), readings={"rh": 31})
    assert not clearly_better("rh:dry", {"rh": 31}) and clearly_better("rh", {"rh": 57}) and not clearly_better("rh", {"rh": 58})



def test_due_in_quiet_hours_waits_until_7():
    p = phone()
    assert step([p], [CO2], datetime(2026, 11, 24, 23, 0, tzinfo=NY)) == []
    assert step([p], [CO2], datetime(2026, 11, 25, 3, 0, tzinfo=NY)) == []
    sends = step([p], [CO2], datetime(2026, 11, 25, 7, 0, tzinfo=NY))
    assert [s.keys for s in sends] == [["co2"]]
    gone = phone()
    step([gone], [CO2], datetime(2026, 11, 24, 23, 0, tzinfo=NY))
    assert step([gone], [], datetime(2026, 11, 25, 6, 0, tzinfo=NY)) == []
    assert step([gone], [], datetime(2026, 11, 25, 7, 0, tzinfo=NY)) == []


def test_window_due_two_hours_before():
    window = item("pm", "Windows shut", "10 AM–1 PM", when_end="2026-11-24T13:00:00-05:00",
                  label="PM2.5 outside", num="40", unit="µg/m³", ctx="167% over the 15 limit")
    day = datetime(2026, 11, 24, tzinfo=NY)
    assert due_at(window, day.replace(hour=7, minute=30), NY) == day.replace(hour=8)
    assert step([phone()], [window], day.replace(hour=7, minute=30)) == []
    assert len(step([phone()], [window], day.replace(hour=8))) == 1


def test_after_form_is_due_at_that_time():
    after = item("co2", "Air out", "after 11 AM")
    now = datetime(2026, 11, 24, 8, 0, tzinfo=NY)
    assert due_at(after, now, NY) == now.replace(hour=11)
    assert due_at(item("co2", "Air out", "after 11:30 AM"), now, NY) == now.replace(hour=11, minute=30)
    assert due_at(item("co2", "Air out", "some new form"), now, NY) == now   # not recognised: due now
    assert due_at(item("co2", None, None), now, NY) is None


def test_new_pm_stretch_is_new_action():
    def pm(end):
        return item("pm", "Windows shut", "until " + end[11:13] + " AM", when_end=f"2026-11-24T{end[11:13]}:00:00-05:00",
                    label="PM2.5 outside", num="40", unit="µg/m³", ctx="167% over the 15 limit")
    first, second = pm("2026-11-24T10"), pm("2026-11-24T11")
    assert action_key(first) != action_key(second) and action_key(item("co2", "x", "now")) == "co2"
    p, t = phone(), datetime(2026, 11, 24, 8, 0, tzinfo=NY)
    assert len(step([p], [first], t)) == 1
    assert step([p], [first], t + timedelta(minutes=30)) == []
    assert len(step([p], [second], t + timedelta(minutes=35))) == 1


def test_offline_once_per_outage():
    p = phone()
    t = datetime(2026, 11, 24, 15, 0, tzinfo=NY)
    last = t - OFFLINE_AFTER - timedelta(minutes=1)
    sends = step([p], [], t, offline={"indoor": last})
    assert [(s.keys, s.title) for s in sends] == [(["offline:indoor"], "Indoor monitor is offline")]
    assert sends[0].body == "No reading since 1:59 PM. Check its power and Wi-Fi. CO₂ and humidity alerts are paused."
    assert step([p], [], t + timedelta(minutes=10), offline={"indoor": last}) == []
    assert p.sent["offline:indoor"].left is None
    step([p], [], t + timedelta(minutes=20))   # back online
    assert p.sent["offline:indoor"].left is not None


def test_offline_started_in_quiet_hours_sends_at_7():
    p = phone()
    down = {"outdoor": datetime(2026, 11, 25, 2, 0, tzinfo=NY)}
    assert step([p], [], datetime(2026, 11, 25, 3, 0, tzinfo=NY), offline=down) == []
    sends = step([p], [], datetime(2026, 11, 25, 7, 0, tzinfo=NY), offline=down)
    assert [s.title for s in sends] == ["Outdoor monitor is offline"] and "since 2 AM" in sends[0].body
    assert step([p], [], datetime(2026, 11, 25, 7, 5, tzinfo=NY), offline=down) == []


def test_offline_muted():
    assert step([phone(muted=frozenset({"offline"}))], [], datetime(2026, 11, 24, 15, 0, tzinfo=NY), offline={"indoor": datetime(2026, 11, 24, 13, 0, tzinfo=NY)}) == []


def test_phone_since_gets_only_new_actions():
    after = item("co2", "Air out", "after 11 AM")
    window = item("pm", "Windows shut", "5–8 PM", when_end="2026-11-24T20:00:00-05:00",
                  label="PM2.5 outside", num="40", unit="µg/m³", ctx="167% over the 15 limit")
    p = phone(since=datetime(2026, 11, 24, 12, 0, tzinfo=NY))
    assert step([p], [after], datetime(2026, 11, 24, 12, 5, tzinfo=NY)) == []   # became due at 11, before this phone
    sends = step([p], [after, window], datetime(2026, 11, 24, 15, 5, tzinfo=NY))   # the window is due from 3 PM
    assert [s.keys for s in sends] == [["pm:2026-11-24T20:00:00-05:00"]]


def test_muted_item_not_sent():
    rh = item("rh", "Run the dehumidifier", "now", label="Humidity inside", num="66", unit="%", ctx="6 points over 60")
    sends = step([phone(muted=frozenset({"co2"}))], [CO2, rh], datetime(2026, 11, 24, 9, 0, tzinfo=NY))
    assert [s.keys for s in sends] == [["rh"]]


def test_quiet_hours_in_home_zone_on_utc_server():
    utc = timezone.utc
    assert in_quiet(datetime(2026, 3, 8, 3, 30, tzinfo=utc), QUIET_DEFAULT, NY)       # 10:30 PM EST the evening before
    assert in_quiet(datetime(2026, 3, 8, 10, 59, tzinfo=utc), QUIET_DEFAULT, NY)      # 6:59 AM EDT: DST began at 2 AM
    assert not in_quiet(datetime(2026, 3, 8, 11, 0, tzinfo=utc), QUIET_DEFAULT, NY)   # 7:00 AM EDT
    assert in_quiet(datetime(2026, 3, 7, 11, 0, tzinfo=utc), QUIET_DEFAULT, NY)       # 6:00 AM EST
    assert not in_quiet(datetime(2026, 3, 7, 12, 0, tzinfo=utc), QUIET_DEFAULT, NY)
    assert quiet_end(datetime(2026, 3, 8, 4, 0, tzinfo=utc), QUIET_DEFAULT, NY) == datetime(2026, 3, 8, 11, 0, tzinfo=utc)
    assert quiet_end(datetime(2026, 3, 8, 12, 0, tzinfo=utc), QUIET_DEFAULT, NY) == datetime(2026, 3, 9, 11, 0, tzinfo=utc)
    p = phone()
    assert step([p], [CO2], datetime(2026, 3, 8, 10, 59, tzinfo=utc)) == []
    assert len(step([p], [CO2], datetime(2026, 3, 8, 11, 0, tzinfo=utc))) == 1


def test_notification_text_forms():
    """The title says what the reading is doing; the body says what to do and when, and the reading against its limit.
    The reasons stay on the page."""
    assert notification_text(CO2) == ("CO₂ is high inside", "Open a window for 10 min (1,240 ppm, over the 800 limit).")   # "now": the arrival says it
    dry = item("rh", "Keep windows shut", "while it’s this dry outside", label="Humidity inside", num="22", unit="%", facts={"dry": True})
    assert notification_text(dry) == ("The air inside is too dry", "Keep windows shut while it’s this dry outside (22%, under the 30% limit).")
    drier = item("rh", "Air the room for 10 min", "drier outside", label="Humidity inside", num="66", unit="%", facts={"drier_out": True})
    assert notification_text(drier) == ("Humidity is high inside", "Air the room for 10 min while it’s drier outside (66%, over the 60% limit).")
    aware = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)
    assert notification_text(item("co2", "Open a window for 10 min", "after 11 AM"), aware)[1] == "Open a window for 10 min (1,240 ppm, over the 800 limit)."
    early = item("co2", "Open a window for 10 min", "now, before the 8 AM rush")
    assert notification_text(early)[1] == "Open a window for 10 min before the 8 AM rush (1,240 ppm, over the 800 limit)."
    later = item("co2", "Open a window for 10 min", "after 11 AM", when_end="2026-03-08T11:00:00+00:00")
    assert notification_text(later, datetime(2026, 3, 8, 9, 0, tzinfo=timezone.utc), timezone.utc)[1] == "Open a window for 10 min after 11 AM (1,240 ppm, over the 800 limit)."
    pollen = item("pollen", "Keep windows shut", "today", label="Pollen today", num="4", unit="of 5", facts={"pollen_type": "tree"})
    assert notification_text(pollen) == ("Tree pollen is high today", "Keep windows shut today.")
    pollen["facts"]["airing_when"] = "when CO₂ is high"
    assert notification_text(pollen)[1] == "Keep windows shut today. You can open a window briefly to lower CO₂."
    smoke = item("pm", "Keep windows shut", "until 3 PM", label="PM2.5 outside", num="80", unit="µg/m³", facts={"cause": "smoke"})
    assert notification_text(smoke) == ("Smoke outside", "Keep windows shut until 3 PM (PM2.5 80 µg/m³, over the 15 limit).")
    window = item("pm", "Keep windows shut", "8–11 AM", label="PM2.5 outside", num="33", unit="µg/m³", forecast=True,
                  facts={"cause": "BQE rush", "peak_at": "9 AM"})
    assert notification_text(window) == ("Traffic exhaust outside 8–11 AM", "Keep windows shut 8–11 AM (PM2.5 up to 33 µg/m³, over the 15 limit).")
    window["facts"] = {}
    assert notification_text(window)[0] == "PM2.5 is high outside 8–11 AM"


def test_later_times_are_on_the_homes_date():
    day = datetime(2026, 11, 24, tzinfo=NY)
    for hour in (13, 15, 20):
        now = day.replace(hour=hour)
        assert due_at(item("co2", "Air out", "after 2 PM"), now, NY) == day.replace(hour=14)
        assert due_at(item("pm", "Windows shut", "4–8 PM"), now, NY) == day.replace(hour=14)   # 4 PM minus 2 hours
        end = "2026-11-24T20:00:00-05:00"
        assert due_at(item("pm", "Windows shut", "4–8 PM", when_end=end), now, NY) == day.replace(hour=14)


def test_overnight_window():
    day = datetime(2026, 11, 24, tzinfo=NY)
    for end, when in (("2026-11-25T01:00:00-05:00", "11 PM–1 AM"), (None, "11 PM–1 AM")):
        w = item("pm", "Windows shut", when, when_end=end)
        assert due_at(w, day.replace(hour=20), NY) == day.replace(hour=21)       # tonight's window
        assert due_at(w, day.replace(hour=23, minute=30), NY) == day.replace(hour=21)
    running = datetime(2026, 11, 25, 0, 30, tzinfo=NY)   # the window began yesterday and still runs
    assert due_at(item("pm", "Windows shut", "11 PM–1 AM", when_end="2026-11-25T01:00:00-05:00"), running, NY) == day.replace(hour=21)
    assert due_at(item("pm", "Windows shut", "11 PM–1 AM"), running, NY) == day.replace(hour=21)


def test_seed_then_only_new_actions():
    rh = item("rh", "Run the dehumidifier", "now", label="Humidity inside", num="66", unit="%", ctx="6 points over 60")
    p, t = phone(), datetime(2026, 11, 24, 9, 0, tzinfo=NY)
    seed(p, [CO2], t, NY, offline={"outdoor": t})
    assert step([p], [CO2], t, offline={"outdoor": t}) == []
    sends = step([p], [CO2, rh], t + timedelta(minutes=30), offline={"outdoor": t})
    assert [s.keys for s in sends] == [["rh"]]


def test_seed_replaces_an_entry_that_left_long_ago():
    """An action sent before that left the Summary over REPEAT_HOLD ago counts as unsent: seeding must record it again,
    or it goes out at the next check (an unmuted item that shows again)."""
    from alert_rules import Sent, Phone, seed, plan_sends
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=2)
    item = {"id": "co2", "label": "CO2", "num": "1,240", "unit": "ppm", "ctx": "", "facts": {},
            "action": {"text": "Open a window", "when": "now", "when_end": None}}
    phone = Phone(id="p", muted=frozenset(), quiet=(22, 7), since=old, sent={"co2": Sent(old, old)})
    seed(phone, [item], now, timezone.utc)
    assert phone.sent["co2"].at == now and phone.sent["co2"].left is None
    assert plan_sends([item], {}, [phone], now + timedelta(minutes=2), timezone.utc) == []


def test_one_notification_per_check():
    p, now = phone(), datetime(2026, 11, 24, 15, 0, tzinfo=NY)
    down = {"indoor": now - timedelta(hours=2), "outdoor": now - timedelta(hours=3)}
    sends = step([p], [CO2], now, offline=down)
    assert len(sends) == 1 and sends[0].keys == ["co2", "offline:indoor", "offline:outdoor"]
    assert sends[0].title == "CO₂ is high inside"
    assert sends[0].body == "Open a window for 10 min (1,240 ppm, over the 800 limit). Also: both monitors are offline."
    again = phone()
    only = step([again], [], now, offline=down)   # no action: the first Offline is the title
    assert len(only) == 1 and only[0].title == "Both monitors are offline" and only[0].keys == ["offline:indoor", "offline:outdoor"]
    assert only[0].body == "No reading since 1 PM. Check their power and the Wi-Fi. CO₂, humidity and PM2.5 alerts are paused."   # the later of 1 PM and 12 PM


def test_a_decimal_limit_reads_as_the_page_writes_it(monkeypatch):
    monkeypatch.setitem(summary.LIMITS, "rh_damp", 60.5)
    damp = item("rh", "Run the dehumidifier", "now", label="Humidity inside", num="66", unit="%", ctx="6 points over 60.5",
                facts={"rh": 66, "dry": False})
    assert notification_text(damp)[1] == "Run the dehumidifier (66%, over the 60.5% limit)."
    monkeypatch.setitem(summary.LIMITS, "co2", 750.5)
    assert notification_text(CO2)[1] == "Open a window for 10 min (1,240 ppm, over the 750.5 limit)."


def test_offline_monitor_items_are_left_out():
    rh = item("rh", "Run the dehumidifier", "now", unit="%")
    pm = item("pm", "Keep the windows shut", "now", unit="µg/m³")
    pollen = item("pollen", "Keep the windows shut", "now", unit="of 5")
    ids = lambda items: [i["id"] for i in items]
    assert ids(without_offline([CO2, rh, pm, pollen], {})) == ["co2", "rh", "pm", "pollen"]
    assert ids(without_offline([CO2, rh, pm, pollen], {"indoor": LONG_AGO})) == ["pm", "pollen"]
    assert ids(without_offline([CO2, rh, pm, pollen], {"outdoor": LONG_AGO})) == ["co2", "rh", "pollen"]


def test_waiting_for_a_baseline_sends_nothing_for_those_items():
    rh = item("rh", "Run the dehumidifier", "now", unit="%")
    now = datetime(2026, 11, 24, 15, 0, tzinfo=NY)
    down = {"indoor": now - timedelta(hours=2)}
    assert plan_sends([CO2, rh], down, [phone(baseline=frozenset({BASELINE_ALL}))], now, NY) == []
    sends = plan_sends([CO2, rh], down, [phone(baseline=frozenset({"co2", "offline"}))], now, NY)
    assert [s.keys for s in sends] == [["rh"]]
    p = phone(baseline=frozenset({"co2"}))
    take_baseline(p, [CO2, rh], now, NY, offline=down)
    assert list(p.sent) == ["co2"]   # only the item that waited
