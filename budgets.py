"""Daily call budgets and failure cool-downs for every outside API, so a bug, a stuck page or a spike of visitors can
never turn into a flood of requests to AirGradient or anyone else.

Every outside call goes through spend(name) first. Each source has a daily ceiling (UTC day); past it, spend() raises
BudgetExceeded (a RuntimeError, so the callers' existing error paths serve the last cached answer) and logs one line.
fail(name, seconds) pauses a source after an answer that won't change by asking again soon (AirGradient refusing the
token, Google Pollen failing). The ceilings sit far above normal use (docs/api-usage.md has the
numbers): they are a circuit breaker, not a rate the app runs at. In memory, like the cache: one gunicorn worker,
so one set of counters."""

import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("aq.budget")

# calls a day per source (one running server). docs/api-usage.md lists them.
DAILY = {
    "ag_current": 5000,     # AirGradient, with the home's token: the monitors' current readings
    "ag_history": 10000,    # AirGradient, with the home's token: a monitor's past readings
    "ag_map_area": 2000,    # AirGradient's map, no token: the public monitors in an area (~6 KB), 15 min per place
    "ag_map": 8000,         # AirGradient's public map: one chosen monitor's place, reading and history
    "pollen": 10,           # Google Pollen (its free tier is capped; one call a day is normal)
    "weather": 2000,        # Open-Meteo forecast
    "air_quality": 1000,    # Open-Meteo air-quality model
    "smoke": 200,           # NOAA HMS smoke polygons
}
AUTH_PAUSE_S = 30 * 60      # s: AirGradient refused the token; asking again won't help until someone fixes it
POLLEN_PAUSE_S = 10 * 60    # s: Google Pollen failed; wait before the next try
# A source that fails (a 5xx, a timeout, no network) waits before the next call: 2 min, doubling while it keeps
# failing, 30 min at most; one success resets it. A 429 or 503 waits what its Retry-After says (default 15 min, 6 h
# at most).
BACKOFF_FIRST_S, BACKOFF_MAX_S = 2 * 60, 30 * 60
RETRY_AFTER_DEFAULT_S, RETRY_AFTER_MAX_S = 15 * 60, 6 * 3600

_lock = threading.Lock()
_day, _used, _paused, _warned = None, {}, {}, set()
_streak, _rate_limited = {}, {}   # failures in a row per source; 429s per source today


class BudgetExceeded(RuntimeError):
    pass


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _roll():
    global _day
    today = _today()
    if today != _day:
        _day = today
        _used.clear()
        _warned.clear()
        _rate_limited.clear()


def spend(name):
    """Count one call to `name`; raise BudgetExceeded instead when its day's budget is used or it's paused."""
    with _lock:
        _roll()
        until = _paused.get(name, 0)
        if until > time.time():
            raise BudgetExceeded(f"{name} paused for {int(until - time.time())} s after a failure")
        limit = DAILY.get(name)
        used = _used.get(name, 0)
        if limit is not None and used >= limit:
            if name not in _warned:
                _warned.add(name)
                log.warning("budget tripped: %s reached %d calls today (UTC); no more calls to it until tomorrow", name, limit)
            raise BudgetExceeded(f"{name} daily budget of {limit} calls used")
        _used[name] = used + 1


def check(name):
    """Raise BudgetExceeded when `name` is paused (no count: the caller counted the call already)."""
    with _lock:
        until = _paused.get(name, 0)
    if until > time.time():
        raise BudgetExceeded(f"{name} paused for {int(until - time.time())} s after a failure")


def fail(name, seconds):
    """Pause `name` for `seconds` (no calls until then)."""
    with _lock:
        _paused[name] = time.time() + seconds
    log.warning("pausing %s for %d s after a failure", name, seconds)


def failed(name):
    """A failed call (5xx, timeout, no network): pause `name` 2 min, then 4, 8 … 30 min while it keeps failing."""
    with _lock:
        n = _streak.get(name, 0)
        _streak[name] = n + 1
        seconds = min(BACKOFF_MAX_S, BACKOFF_FIRST_S * 2 ** n)
        _paused[name] = max(_paused.get(name, 0), time.time() + seconds)
    log.warning("backing off %s for %d s (failure %d in a row)", name, seconds, n + 1)


def succeeded(name):
    """A good answer: the failure streak ends (one log line when it was backing off)."""
    with _lock:
        was = _streak.pop(name, 0)
    if was:
        log.info("%s answering again after %d failure(s)", name, was)


def retry_after(header, now=None):
    """Seconds to wait from a Retry-After header (seconds or an HTTP date): the default when missing or unreadable,
    never over RETRY_AFTER_MAX_S, never negative."""
    from email.utils import parsedate_to_datetime
    if header is None or str(header).strip() == "":
        return RETRY_AFTER_DEFAULT_S
    text = str(header).strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(text).timestamp() - (now or time.time())
        except (TypeError, ValueError, IndexError, OverflowError):
            return RETRY_AFTER_DEFAULT_S
    return int(min(RETRY_AFTER_MAX_S, max(0, seconds)))


def told_to_wait(names, status, header):
    """A 429 or 503: pause every source in `names` (the ones on that host) for Retry-After's time. A 429 also logs an
    alert line and is counted in usage(): AirGradient (or anyone) asking us to slow down must never go unnoticed."""
    seconds = retry_after(header)
    with _lock:
        _roll()
        for name in names:
            _paused[name] = max(_paused.get(name, 0), time.time() + seconds)
            if status == 429:
                _rate_limited[name] = _rate_limited.get(name, 0) + 1
    if status == 429:
        log.error("ALERT rate limited (HTTP 429) on %s: no calls for %d s, as Retry-After asks", ", ".join(names), seconds)
    else:
        log.warning("%s answered %d: no calls for %d s", ", ".join(names), status, seconds)
    return seconds


def clear(name=None):
    """Lift a pause (a new token was saved), or every pause and count (tests)."""
    with _lock:
        if name is None:
            _used.clear(); _paused.clear(); _warned.clear(); _streak.clear(); _rate_limited.clear()
            import upstream   # its hosts' 429/503 pauses too
            upstream._HOST_UNTIL.clear()
        else:
            _paused.pop(name, None)
            _streak.pop(name, None)


def usage():
    """{"day": "YYYY-MM-DD", "used": {name: calls}, "budget": DAILY, "paused": {name: seconds left},
    "rate_limited": {name: 429s today}}"""
    with _lock:
        _roll()
        now = time.time()
        return {"day": _day, "used": dict(_used), "budget": dict(DAILY),
                "paused": {k: int(v - now) for k, v in _paused.items() if v > now}, "rate_limited": dict(_rate_limited)}
