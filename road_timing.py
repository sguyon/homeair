"""When a road's exhaust reaches the home: its busy hours, the flag ("reaches you now"), its strength and until when.

Busy hours, the first that exists: the hand-set road's `rush` (every day); on a weekday, the road's slow hours measured
from its speeds (`slow` in [[roads]]); the defaults by class. At the same flow a slower road holds more vehicles per
stretch (density = flow / speed, traffic-flow arithmetic); slow hours stand for stop-and-go traffic. Barth &
Boriboonsomsin (2008) studied CO₂, not NO₂ or ultrafine particles. The flag is on inside
the busy hours with the wind from the road's `toward` at CALM_KMH or more, or on a calm morning with the wind from its
`toward_calm` in the two hours before sunrise. Hu et al. (2009) measured the long reach 1–2 h before sunrise and a
return to background within about 300 m after sunrise. No network; how the hours are measured:
scripts/roads/speeds.py and docs/roads.md."""
from datetime import date, datetime, time, timedelta, timezone
from itertools import zip_longest

from roads import DEFAULT_RUSH, reaches

CALM_KMH = 7  # km/h, about 2 m/s: an unverified cutoff for the calm-reach rule
CALM_BEFORE_SUNRISE_H = 2  # h: Hu et al. 2009 measured long reach 1–2 h before sunrise; background within about 300 m after
SLOW_HOURS = (5, 22)  # [from, to) hours where a road can be slow: keeps out the midnight lane closures
SLOW_SHARE = 0.5  # an hour is slow when its median speed is under this share of the road's fastest hourly median
DEFAULT_BUSY_US = {"highway": ((6, 22),), "main road": ((7, 20),)}  # [from, to) busy hours by kind in the US (estimate, from the BQE's count profile)
SHOULDER_H = 1  # hours either side of a rush or slow window that count as "high"
UNTIL_HOURS = 12  # how far the forecast is read for the time a flag turns off


def busy_windows(road, weekday: bool) -> tuple[tuple[tuple[int, int], ...], str]:
    """The road's busy hours as [from, to) windows, and where they come from: "rush" (hand-set, every day), "slow"
    (measured, weekdays only) or "busy" (the class defaults: DEFAULT_BUSY_US in the US, else DEFAULT_RUSH's hours)."""
    if road.rush:
        return tuple((a, b) for a, b, *_ in road.rush), "rush"
    if weekday and road.slow:
        return tuple(tuple(w) for w in road.slow), "slow"
    if road.country == "US":
        return DEFAULT_BUSY_US.get(road.kind, DEFAULT_BUSY_US["highway"]), "busy"
    return tuple((a, b) for a, b, _ in DEFAULT_RUSH), "busy"


def _instant(at):
    """UTC for elapsed-time arithmetic; naive times retain their original convention."""
    return at.astimezone(timezone.utc) if at.tzinfo else at


def real_sunrises(sunrises, sunsets):
    """Exclude Open-Meteo's no-transit midnight placeholders (polar night/day).
    https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Helper/Solar/SunRiseSet.swift
    Inputs retain daily slots, including missing entries. Polar night has rise == set == midnight;
    polar day has midnight rise and next-midnight set. Only that slot's sunset can validate a midnight rise.
    """
    return [rise for rise, sunset in zip_longest(sunrises, sunsets) if rise is not None
            and (rise.time() != time.min or (sunset is not None
                 and sunset not in (rise, rise + timedelta(days=1))))]


def sunrise_for(at, sunrises):
    """Prefer the next real sunrise, including after midnight, for the calm window. Today's past sunrise is a fallback
    when the forecast ends; a past sunrise cannot activate calm reach."""
    rises = list(sunrises)
    upcoming = min((s for s in rises if _instant(s) >= _instant(at)), key=_instant, default=None)
    return upcoming or next((s for s in rises if s.date() == at.date()), None)


def road_now(road, at: datetime, wind_dir: float | None, wind_kmh: float | None, sunrise: datetime | None) -> dict:
    """{"reaching", "strength", "why"} for one road at a local time. Missing wind turns the flag off; calm needs sunrise.
    `why` is "calm", else the busy hours' source inside them,
    else None. Strength comes from the hours unless calm reach applies."""
    windows, source = busy_windows(road, at.weekday() < 5)
    h = at.hour + at.minute / 60
    in_busy = any(a <= h < b for a, b in windows)
    peak_hours = source in ("rush", "slow")
    shoulder = peak_hours and not in_busy and any(a - SHOULDER_H <= h < a or b <= h < b + SHOULDER_H for a, b in windows)
    known = wind_dir is not None and wind_kmh is not None
    busy_flag = known and in_busy and wind_kmh >= CALM_KMH and road.toward is not None and reaches(road.toward, wind_dir)
    calm = (known and sunrise is not None and wind_kmh < CALM_KMH
            and _instant(sunrise) - timedelta(hours=CALM_BEFORE_SUNRISE_H) <= _instant(at) < _instant(sunrise)
            and road.toward_calm is not None and reaches(road.toward_calm, wind_dir))
    strength = "peak" if calm or (in_busy and peak_hours) else "high" if in_busy or shoulder else "low"
    return {"reaching": bool(busy_flag or calm), "strength": strength, "why": "calm" if calm else source if in_busy else None}


def _reach_events(roads, at, hourly, sunrises):
    """Yield (time, road states) now and at wind/traffic/solar boundaries for UNTIL_HOURS.
    Read forecast wind only at its own instant. Unknown wind yields None and ends the prediction.
    """
    start = _instant(at)
    end = start + timedelta(hours=UNTIL_HOURS)
    rows = sorted(hourly, key=lambda r: _instant(r["time"]))
    previous = next((r for r in reversed(rows) if _instant(r["time"]) <= start), None)
    wind = previous or {}

    def states(t):
        sunrise = sunrise_for(t, sunrises.values())
        return [road_now(r, t, wind.get("wind_dir"), wind.get("wind_kmh"), sunrise) for r in roads]

    active = states(at)
    yield at, active
    events = {_instant(r["time"]): ("wind", r) for r in rows if start < _instant(r["time"]) <= end}
    for sunrise in sunrises.values():
        for t in (_instant(sunrise) - timedelta(hours=CALM_BEFORE_SUNRISE_H), _instant(sunrise)):
            if start < t <= end:
                events.setdefault(t, ("solar", None))
    # Use local dates for traffic schedules, UTC for ordering and the forecast horizon.
    day = at.replace(hour=0, minute=0, second=0, microsecond=0)
    while _instant(day) <= end:
        for road in roads:
            windows, _ = busy_windows(road, day.weekday() < 5)
            for a, b in windows:
                for hour in (a, b):
                    t = _instant(day + timedelta(hours=hour))
                    if start < t <= end:
                        if t not in events or events[t][0] == "solar":
                            events[t] = ("busy", None)
        day += timedelta(days=1)
    for instant, (kind, row) in sorted(events.items()):
        t = instant.astimezone(at.tzinfo) if at.tzinfo else instant
        if kind == "solar" and wind.get("wind_kmh") is not None and wind["wind_kmh"] >= CALM_KMH:
            continue  # A solar boundary says nothing about a busy-only action's wind.
        if row is not None:
            wind = row
        if wind.get("wind_dir") is None or wind.get("wind_kmh") is None:
            yield t, None
            return
        active = states(t)
        yield t, active


def _first_off(roads, at: datetime, hourly: list[dict], sunrises: dict[date, datetime]) -> datetime | None:
    """First boundary after now with every road off; unknown wind prevents an end time."""
    events = _reach_events(roads, at, hourly, sunrises)
    next(events)  # Callers ask about roads reaching now; only a later boundary can end that action.
    for t, active in events:
        if active is None:
            return None
        if not any(r["reaching"] for r in active):
            return t
    return None


def reaching_before(roads, at, until, hourly, sunrises):
    """Road indexes reaching before `until`, using the same wind and boundaries as all_until."""
    found = set()
    for t, active in _reach_events(roads, at, hourly, sunrises):
        if _instant(t) >= _instant(until) or active is None:
            break
        found.update(k for k, state in enumerate(active) if state["reaching"])
    return found


def road_until(road, at: datetime, hourly: list[dict], sunrises: dict[date, datetime]) -> datetime | None:
    """For a road reaching now: its first wind, busy-hour or sunrise end within UNTIL_HOURS, else None.
    `hourly` rows: {"time", "wind_dir", "wind_kmh"}, including current wind at `at` when known;
    `sunrises`: real sunrises by local date, with polar placeholders removed.
    """
    return _first_off([road], at, hourly, sunrises)


def all_until(roads: list, at: datetime, hourly: list[dict], sunrises: dict[date, datetime]) -> datetime | None:
    """The first wind, busy-hour or sunrise end with no road reaching, else None, as road_until."""
    return _first_off(roads, at, hourly, sunrises)
