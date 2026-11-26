"""Outside sources, their shared in-memory cache, and monitor history."""

import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import request
import requests

import budgets
import phrases
import publicmap
import upstream
import summary as summary_rules
import config
import history_store
import localnet
from config import Here, airgradient_map, env_key

# Config and the Flask app stay owned by server; startup passes its module as runtime.
# Functions read it at call time so use_config() never leaves a copied config behind.

# One shared pool for short-lived upstream calls (weather / smoke / pollen).
_UPSTREAM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upstream")

# ── Server-side cache ──
# One in-memory store for upstream data (AirGradient, Open-Meteo, Google Pollen, NOAA).
# - fresh (age < ttl): served as is
# - a little old (age < ttl + swr): served at once, refreshed in the background
# - older or missing: fetched; concurrent requests for the same key share one fetch
# - fetch fails: the last good value is served if it's younger than `keep`
# Fetch functions must raise on failure (so a bad answer is never stored) and must not call cached() themselves.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("aq.cache")
_REFRESH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cache")   # background refreshes of stale cache entries
_cache, _inflight, _cache_lock = {}, {}, threading.Lock()

API_BASE = "https://api.airgradient.com/public/api/v1"   # AirGradient's public API: the home's monitors
# A stand-in for AirGradient's API (a test server with made-up monitors), for simulated setups; unset everywhere else
API_BASE = os.environ.get("AIRGRADIENT_API_BASE", "").rstrip("/") or API_BASE
NEARBY_RADIUS_KM = 15   # km: public monitors closer than this show as Nearby monitors (the source note says 15 km)
MAP_LINK_DECIMALS = 4   # a neighbor's link on AirGradient's map: its coordinates to about 10 m (the map gives more digits)
SECRET_ENV = ("AIRGRADIENT_TOKEN", "GOOGLE_POLLEN_API_KEY")   # keys that ride in request URLs, so in error text
# NOAA's Hazard Mapping System: the day's smoke polygons, one KML file per day
HMS_KML = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/KML/{y}/{m}/hms_smoke{d}.kml"
SMOKE_RANK = {"Light": 1, "Medium": 2, "Heavy": 3}   # NOAA HMS densities, lightest first: a place gets its densest
# The local dev server (`flask run --reload`, which sets WERKZEUG_RUN_MAIN in the process that serves) restarts on every
# .py edit and would ask Google for the day's pollen again each time: with the Pollen API capped at 150 calls a day
# (SETUP.md), a day of edits could use them up. There it keeps the day's answer in a gitignored file. Production
# (gunicorn) never sets the variable and stores nothing.
DEV_CACHE_DIR = Path(__file__).parent / ".cache"
HIST_KEEP = ("timestamp", "pm02", "pm02_corrected", "rco2", "rco2_corrected", "rhum", "rhum_corrected")   # history fields the charts and rules read
# ── How often AirGradient is asked (docs/api-usage.md) ──
# The monitors' readings are fetched at most once per REFRESH_SECONDS, shared by every open tab and visitor: a page
# reads the server's cache, so the calls grow with homes, not with people looking. Nothing is fetched while no one has
# the app open and no phone has alerts on (alerts.py checks every ALERT_CHECK_SECONDS, through these same caches). AirGradient's history comes in 5-minute buckets, so 5 minutes is as fresh as a chart can get; a Wi-Fi
# monitor posts every minute, which is why REFRESH_FLOOR_SECONDS (and the Refresh button's floor) is one minute.
REFRESH_FLOOR_SECONDS = 60


def refresh_seconds(env=os.environ):
    """AIRGRADIENT_REFRESH_SECONDS (default 300), never under REFRESH_FLOOR_SECONDS; junk reads as the default."""
    try:
        value = int(env.get("AIRGRADIENT_REFRESH_SECONDS") or 300)
    except ValueError:
        value = 300
    return max(REFRESH_FLOOR_SECONDS, value)


REFRESH_SECONDS = refresh_seconds()
# the History ranges (hours) and how long each is cached: (fresh, then served stale while refreshing), in seconds.
# 1, 6 and 24 h are cut from one day of rows per monitor (HIST_DAY), extended with only the readings since its newest
# one; the week is asked again at most hourly.
HIST_TTL = {1: (REFRESH_SECONDS, 0), 6: (REFRESH_SECONDS, 0), 24: (REFRESH_SECONDS, 0), 168: (3600, 3600)}
HIST_DAY = 24   # h: the rows kept per monitor for the ranges up to a day
HIST_OVERLAP = timedelta(minutes=10)   # an extension starts this far before the newest row: its bucket may have grown
HIST_SLACK = timedelta(minutes=2)   # how far a History range's start may sit before its hours and still read as that range
# Public monitors near the home come from AirGradient's map, one area request (publicmap.fetch_area, about 6 KB) that
# lists the monitors within AREA_SEARCH_KM with their PM2.5 now. It is asked only when something needs it (the Nearby
# card shown, an indoor-only home's area estimate) and at most every NEARBY_REFRESH_SECONDS (15 min, the Refresh button
# included): nearby monitors are context, not the home's own readings. Nothing downloads every monitor in the world.
NEARBY_REFRESH_SECONDS = 15 * 60
AREA_SEARCH_KM = max(NEARBY_RADIUS_KM, publicmap.AREA_KM) + 1   # km each way: one box for Nearby and the area estimate
AREA_CACHE = {"ttl": NEARBY_REFRESH_SECONDS, "keep": 3600, "force_floor": NEARBY_REFRESH_SECONDS, "wait": publicmap.MAP_WAIT}
# ── An indoor-only home's outdoor PM2.5 ([sources] outdoor_from) ────────
# A public monitor's id (its reading and its history on AirGradient's map, like an own outdoor monitor), "area" (the
# public monitors around tune the model) or "model" (Open-Meteo's alone). Chosen once at setup; when the chosen monitor
# isn't reporting, the area stands in (fallback), and the model when the area can't. publicmap.py has the radii.
AREA_MAX_MONITORS = 5   # the nearest area monitors read: a median over 5 shrugs off one bad sensor, in few map requests
SOURCE_HOURS = 24   # h: the history a source is read over, the day calibrate_forecast compares the model with
# s: the most one request waits on the map in all for its source; past the budget, what isn't cached yet goes on in
# the background for the next request
SOURCE_BUDGET = 10
# a chosen monitor's name and place (s): they rarely change, so a stale one is served while it refreshes, for a day more
PLACE_CACHE = {"ttl": publicmap.LOCATION_TTL, "swr": publicmap.LOCATION_TTL, "keep": 7 * 24 * 3600}
# a source's day of history for the summary and the area (s): fresh as publicmap.HISTORY_TTL, then served at once
# while it refreshes for up to 3 h (a day's ratio barely moves in that time), so a warm-ish cache never makes a
# request wait on the map; kept 3 h when the map fails
SOURCE_HIST_CACHE = {"ttl": publicmap.HISTORY_TTL, "swr": 3 * 3600, "keep": 3 * 3600}
# the History card's ranges of a public monitor (s): served stale for 30 min at most, as the chart shows the latest hour
CHART_HIST_CACHE = {"ttl": publicmap.HISTORY_TTL, "swr": 1800, "keep": 3 * 3600}
# what "AirGradient didn't answer" means: the request failed (network, HTTP status, bad JSON) or ran out of time
SOURCE_ERRORS = (requests.RequestException, RuntimeError, TimeoutError)
# the facts the page and the summary get about a source; the row and the history stay on the server
SOURCE_FACTS = ("kind", "id", "name", "street", "contributor", "url", "distance_km", "area_count", "fallback")
_SOURCE_SEEN = {}   # public monitor id → its last row, to name it while the map doesn't answer


def _call_with_deadline(fn, *args, deadline_s=5, default=None, **kwargs):
    """Run fn with a hard wall-clock deadline.

    Needed because macOS getaddrinfo can hang well past requests' connect timeout
    (seen with pollen.googleapis.com DNS stalls ~30s).
    """
    fut = _UPSTREAM_POOL.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=deadline_s)
    except FuturesTimeout:
        fut.cancel()
        return default
    except budgets.BudgetExceeded:   # over budget or paused: no call was made, so no failure to remember
        raise
    except Exception:
        return default


def _cache_run(key, fetch):
    t0 = time.time()
    try:
        value = fetch()
    except budgets.BudgetExceeded:   # no call was made; budgets.py logged the pause or the cap once
        raise
    except Exception as e:
        log.warning("upstream %s failed after %.2fs: %s", key, time.time() - t0, _redact(e)[:160])
        raise
    finally:
        with _cache_lock:
            _inflight.pop(key, None)
    with _cache_lock:
        _cache[key] = (time.time(), value)
        if len(_cache) > 200:   # drop entries untouched for 2 h (old summaries, past days' pollen)
            for k in [k for k, (at, _) in _cache.items() if time.time() - at > 7200]:
                _cache.pop(k, None)
    log.info("upstream %s ok %.2fs", key, time.time() - t0)
    return value


def _cache_start(key, fetch):
    with _cache_lock:
        fut = _inflight.get(key)
        if fut is None:
            fut = _inflight[key] = _REFRESH_POOL.submit(_cache_run, key, fetch)
        return fut


def cached(key, fetch, ttl, swr=0, keep=0, force=False, force_floor=15, wait=15):
    """Returns (value, info) with info = {"state": "hit"|"stale"|"miss"|"fallback", "age": seconds}."""
    with _cache_lock:
        entry = _cache.get(key)
    age = time.time() - entry[0] if entry else None
    if entry and not (force and age > force_floor):
        if age < ttl:
            return entry[1], {"state": "hit", "age": age}
        if age < ttl + swr:
            _cache_start(key, fetch)          # refresh in the background, answer now
            return entry[1], {"state": "stale", "age": age}
    try:
        value = _cache_start(key, fetch).result(timeout=wait)
        return value, {"state": "miss", "age": 0}
    except Exception:
        if entry and age < keep:
            return entry[1], {"state": "fallback", "age": age}
        raise


_shared_cached = cached   # the server's own cache, even where the demo replaces `cached` for its seams (area_monitors)


def _force():
    """The page's Refresh button sends X-Force-Refresh: 1."""
    # Each source has a floor (15 s, 10 min for Google Pollen) so repeats don't refetch.
    try:
        return request.headers.get("X-Force-Refresh") == "1"
    except RuntimeError:   # outside a request
        return False


def home_here():
    """The configured home as a Here (key "home": its cache keys stay the ones they always were)."""
    return Here("home", runtime.HOME_LAT, runtime.HOME_LON, runtime.CFG.place.timezone if runtime.CFG and runtime.CFG.place else None)


def _cache_key(base, here):
    """The home keeps its plain cache key; any other place adds its own."""
    return base if here.key == "home" else f"{base}:{here.key}"


def local_now(here=None):
    """The clock at home, or at another place. Railway runs in UTC, so a bare datetime.now() gives the wrong hour there."""
    tz = ZoneInfo(here.tz) if here is not None and here.tz else runtime.HOME_TZ
    return datetime.now(tz) if tz else datetime.now(timezone.utc)


def _redact(text):
    """Keys never leave the server: error text can carry the request URL (?token=..., ?key=...) or echo it.
    Used for every error the server logs or returns."""
    text = str(text)
    for k in [runtime.TOKEN] + [env_key(name) for name in SECRET_ENV]:
        if k:
            text = text.replace(k, "***")
    return text


class _TokenURLFilter(logging.Filter):
    """urllib3's optional debug output includes request URLs; strip query credentials before any handler sees it."""
    def filter(self, record):
        record.msg = re.sub(r"([?&](?:token|key)=)[^&\s\"']+", r"\1***", record.getMessage())
        record.args = ()
        return True


logging.getLogger("urllib3.connectionpool").addFilter(_TokenURLFilter())


def validate_token(token):
    """One uncached call, without changing the running credential or exposing upstream bodies/errors.
    None: AirGradient refused the token. A list (maybe empty): accepted, with the account's rows as AirGradient sent
    them (dicts only): their ids, names, kinds and serials match the home's monitors (match_account) and name them on
    the page's "connected" card (account_monitors)."""
    response = requests.get(f"{API_BASE}/locations/measures/current", params={"token": token}, timeout=10, allow_redirects=False,
                            headers=upstream.headers())
    if response.status_code in (400, 401, 403):
        return None
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Unexpected AirGradient status")
    # An account with no monitors is still a valid account; malformed responses aren't credentials we can verify.
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Unexpected AirGradient response")
    return [row for row in rows if isinstance(row, dict)]


def account_monitors(rows):
    """The account's monitors for the "connected" card: {"name": ..., "kind": "indoor" | "outdoor" | None}."""
    return [{"name": publicmap.plain_text(row.get("locationName") or "") or "Unnamed monitor", "kind": monitor_kind(row)}
            for row in rows]


class MonitorChoice(ValueError):
    """Today's rule can't pick the account's monitor for a kind by itself; the message says what to choose."""


# AirGradient model names starting "P-" are portable (the AirGradient Go, P-1PSG): a monitor that moves with its owner
# is never the home's indoor or outdoor monitor unless its id was chosen.
PORTABLE_PREFIX = "P-"


def _portable(row):
    return str(row.get("model") or "").startswith(PORTABLE_PREFIX)


def _cloud_id(row):
    value = row.get("locationId")
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def choose_monitors(rows, indoor_id=None, outdoor_id=None, kinds=config.MONITOR_KINDS):
    """Today's rule, one account monitor per kind: the one with the id given, else the only one of that kind (a portable
    Go counts only when given by id). Monitors
    whose kind isn't set in AirGradient count only when given by id, or as the only candidate for neither kind.
    {kind: row}; MonitorChoice when it can't choose by itself, or chooses nothing. The same rule as setup's."""
    wanted_ids = {"indoor": indoor_id, "outdoor": outdoor_id}
    wanted = {str(i) for i in wanted_ids.values() if i is not None}
    portable = [r for r in rows if _portable(r) and str(r.get("locationId")) not in wanted]
    rows = [r for r in rows if r not in portable]
    for kind in kinds:   # a Go as the only monitor of a kind: name it and its flag, never "no monitors" or a silent skip
        if wanted_ids[kind] is None and not any(r.get("locationType") == kind for r in rows):
            go = next((r for r in portable if r.get("locationType") in (kind, None)), None)
            if go is not None:
                raise MonitorChoice(f"The only {kind} monitor is a portable AirGradient Go ({go.get('locationId')}: "
                                    f"{publicmap.plain_text(go.get('locationName') or 'Unnamed monitor')}). Home Air "
                                    f"uses a portable monitor only when chosen: pass --{kind}-id {go.get('locationId')}.")
    chosen = {}
    unknown = [r for r in rows if r.get("locationType") not in ("indoor", "outdoor")]
    for kind in kinds:
        other = "outdoor" if kind == "indoor" else "indoor"
        available_unknown = [r for r in unknown if str(r.get("locationId")) != str(wanted_ids[other])]
        candidates = [r for r in rows if r.get("locationType") == kind]
        wanted = wanted_ids[kind]
        options = candidates + available_unknown
        match = [r for r in options if str(r.get("locationId")) == str(wanted)] if wanted is not None else []
        if len(match) == 1:
            chosen[kind] = {**match[0], "locationType": kind}
        elif wanted is not None or len(candidates) > 1 or (available_unknown and not candidates):
            words = "; ".join(f"{r.get('locationId')}: {publicmap.plain_text(r.get('locationName') or 'Unnamed monitor')}" for r in options)
            raise MonitorChoice(f"Choose one of: {words or 'no matching monitors'}. Pass --{kind}-id <id>. "
                                "Set uncertain monitor types in AirGradient's dashboard, then run setup again.")
        elif candidates:
            chosen[kind] = candidates[0]
        if kind in chosen:
            unknown = [r for r in unknown if r.get("locationId") != chosen[kind].get("locationId")]
    if not chosen:
        raise MonitorChoice("AirGradient lists no monitors with readings in this account. A monitor shows up once it has sent its first reading: check it shows readings in the AirGradient app, then run setup again.")
    if len({str(r.get("locationId")) for r in chosen.values()}) != len(chosen):
        raise MonitorChoice("Choose a different monitor for indoors and outdoors in AirGradient.")
    if not all(_cloud_id(row) for row in chosen.values()):
        raise MonitorChoice("AirGradient returned a monitor without a valid id. Check it in its dashboard.")
    return chosen


def match_account(cfg, rows):
    """After a token: the [place] keys that tie the home's monitors to the account's (for config.set_monitor_keys), by
    kind. A monitor with a serial gets the cloud id of the account's row with that serial (in any case), and its cloud
    name unless the household renamed it (a name other than phrases.PLACE_DEFAULTS'). A kind without a serial gets
    today's rule (choose_monitors) among the rows not matched; when the rule can't choose by itself, that kind is left
    as it is.
    A serial the account doesn't have (when its rows carry serials) isn't the household's to assume: on a shared Wi-Fi
    it can be a neighbour's. The account's monitor of that kind takes its place (its serial, so it is read on the Wi-Fi
    when it is there, and its id; a fixed address goes, it was the other monitor's); when the rule can't pick one, the
    serial stays, without an id that belongs to another monitor."""
    place = cfg.place
    rows = [r for r in rows if isinstance(r, dict) and _cloud_id(r)]
    serial_of = lambda r: str(r.get("serialno") or "").strip().lower()
    account_serials = any(serial_of(r) for r in rows)

    def keys(kind, row):
        kv = {f"{kind}_id": row["locationId"]}
        name = getattr(place, kind)
        cloud = publicmap.plain_text(row.get("locationName") or "")
        if name is None or name == phrases.PLACE_DEFAULTS[kind]:
            kv[kind] = cloud or phrases.PLACE_DEFAULTS[kind]
        return kv

    out, strangers = {}, []
    for kind in config.MONITOR_KINDS:
        serial = getattr(place, kind + "_serial")
        row = next((r for r in rows if serial and serial_of(r) == serial), None)
        if row is not None:
            out[kind] = keys(kind, row)
        elif serial and account_serials:
            strangers.append(kind)
    used = {kv[f"{kind}_id"] for kind, kv in out.items()}
    missing = tuple(k for k in config.MONITOR_KINDS if getattr(place, k + "_serial") is None or k in strangers)
    chosen = {}
    if missing:
        try:
            chosen = choose_monitors([r for r in rows if r["locationId"] not in used], kinds=missing,
                                     **{f"{k}_id": None if k in strangers else getattr(place, k + "_id") for k in missing})
        except MonitorChoice:
            chosen = {}
        out.update({kind: keys(kind, row) for kind, row in chosen.items()})
    for kind in strangers:
        if kind in chosen:
            out[kind].update({f"{kind}_serial": serial_of(chosen[kind]) or None, f"{kind}_host": None})
            log.warning("the %s monitor found on the Wi-Fi isn't in this AirGradient account: the account's %s monitor "
                        "is read instead", kind, kind)
        else:
            out[kind] = {f"{kind}_id": None}
            log.warning("the %s monitor found on the Wi-Fi isn't in this AirGradient account: it stays read on the "
                        "Wi-Fi, without the account's history", kind)
    return out


def monitor_kind(row):
    """Indoor or outdoor, from the place's setting in AirGradient, else the model ("O-…" is an Open Air); None if neither says."""
    if row.get("locationType") in ("indoor", "outdoor"):
        return row["locationType"]
    model = str(row.get("model") or "")
    return "outdoor" if model.startswith(publicmap.OUTDOOR_PREFIX) else "indoor" if model.startswith("I-") else None


def api_budget(path):
    """The budget an AirGradient token call counts against: history ("/measures/past") or current readings."""
    return "ag_history" if path.rstrip("/").endswith("/measures/past") else "ag_current"


def api_get(path, params=None):
    p = {"token": runtime.TOKEN}
    if params:
        p.update(params)
    try:
        r = upstream.get(f"{API_BASE}{path}", api_budget(path), params=p, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        if status == 401:   # a refused token pauses both token budgets: asking again won't help until a new token is saved
            budgets.fail("ag_current", budgets.AUTH_PAUSE_S)
            budgets.fail("ag_history", budgets.AUTH_PAUSE_S)
        raise RuntimeError(_redact(f"AirGradient API returned {status}: {e.response.text[:300]}")) from None
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"AirGradient API unreachable ({type(e).__name__})") from None


def _fetch_weather(here):
    r = upstream.get(
        "https://api.open-meteo.com/v1/forecast", "weather",
        params={
            "latitude": here.lat,
            "longitude": here.lon,
            "current": "temperature_2m,relative_humidity_2m,dew_point_2m,weather_code,wind_speed_10m,wind_direction_10m,visibility,uv_index,cloud_cover",
            "hourly": "wind_direction_10m,wind_speed_10m,precipitation_probability,precipitation,weather_code,temperature_2m,dew_point_2m,uv_index",
            "forecast_hours": 24,
            "past_hours": 4,   # the summary chart starts before now
            "daily": "sunrise,sunset",
            "forecast_days": 2,
            "timezone": here.tz
        },
        timeout=10
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("current"):
        raise RuntimeError("Open-Meteo answered without current weather")
    return data


def get_open_meteo_data(here=None):
    """Weather from Open-Meteo (free, no key) at a place (the home by default). Their current data moves on a 15-minute grid."""
    here = here or home_here()
    if here.lat is None:   # a home without a place yet: nothing to ask Open-Meteo about
        return no_weather()
    try:
        value, info = cached(_cache_key("wx", here), lambda: _fetch_weather(here), ttl=600, swr=3600, keep=3 * 3600, force=_force(), force_floor=300)
        return value
    except Exception:
        return no_weather()


def no_weather():
    """The weather's answer when Open-Meteo can't be reached: every reading None."""
    return {
        "current": {"wind_speed_10m": None, "wind_direction_10m": None, "visibility": None, "uv_index": None, "dew_point_2m": None},
        "hourly": {"wind_direction_10m": [], "wind_speed_10m": []}
    }


def _fetch_air_forecast(here):
    """Outdoor PM2.5 for the next 24 hours from Open-Meteo's air-quality model (CAMS, free, no key)."""
    r = upstream.get(
        "https://air-quality-api.open-meteo.com/v1/air-quality", "air_quality",
        params={"latitude": here.lat, "longitude": here.lon, "hourly": "pm2_5",
                "forecast_hours": 24, "past_hours": 24,   # the past day calibrates the forecast to the backyard monitor
                "timezone": here.tz},
        timeout=10
    )
    r.raise_for_status()
    hourly = r.json().get("hourly") or {}
    times, vals = hourly.get("time") or [], hourly.get("pm2_5") or []
    if not times:
        raise RuntimeError("Open-Meteo air quality answered without hours")
    return dict(zip(times, vals))


def get_air_forecast(here=None):
    """{"2026-11-07T18:00": pm2.5, ...}; empty when the model can't be reached. A model, so it can sit above or
    below the monitor: the page reacts to how it changes, not just its level."""
    here = here or home_here()
    if here.lat is None:
        return {}
    try:
        value, _ = cached(_cache_key("aq", here), lambda: _fetch_air_forecast(here), ttl=1800, swr=3600, keep=6 * 3600, force=_force(), force_floor=300)
        return value
    except Exception:
        return {}


def _point_in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def smoke_status(here=None):
    """Whether NOAA HMS mapped wildfire smoke over a place (the home by default), from today's file (or yesterday's until today's is posted).

    Returns {"checked": bool, "detected": bool, "density": "Light"|"Medium"|"Heavy"|None, "date": "YYYY-MM-DD"|None}.
    The day's polygons are one file for all of North America, so they're cached once ("smoke") and each place is
    tested against them here, in the caller's thread.
    """
    here = here or home_here()
    if here.lat is None:
        return no_smoke()
    try:
        value, _ = cached("smoke", _fetch_smoke, ttl=1800, swr=6 * 3600, keep=24 * 3600, force=_force(), force_floor=600)
        return smoke_at(value, here)
    except Exception:
        return no_smoke()


def no_smoke():
    """The smoke answer when NOAA wasn't checked."""
    return {"checked": False, "detected": False, "density": None, "date": None}


def _fetch_smoke():
    """Today's NOAA HMS file, or yesterday's until today's is posted, parsed: {"date", "polygons": [(density, ring)]}
    with each ring a list of (lon, lat). Raises when neither loads."""
    now = datetime.now(timezone.utc)
    for back in (0, 1):
        day = now - timedelta(days=back)
        url = HMS_KML.format(y=day.strftime("%Y"), m=day.strftime("%m"), d=day.strftime("%Y%m%d"))
        try:
            r = upstream.get(url, "smoke", timeout=12)
        except requests.exceptions.RequestException:
            continue
        if r.status_code != 200 or "<Placemark>" not in r.text:
            continue
        polygons = []
        for mark in re.findall(r"<Placemark>(.*?)</Placemark>", r.text, re.S):
            dens = re.search(r"Density:\s*(Light|Medium|Heavy)", mark)
            for coords in re.findall(r"<coordinates>(.*?)</coordinates>", mark, re.S):
                ring = []
                for triple in coords.split():
                    parts = triple.split(",")
                    if len(parts) >= 2:
                        ring.append((float(parts[0]), float(parts[1])))
                if len(ring) > 2:
                    polygons.append((dens.group(1) if dens else "Light", ring))
        return {"date": day.strftime("%Y-%m-%d"), "polygons": polygons}
    raise RuntimeError("No NOAA HMS smoke file for today or yesterday")


def smoke_at(hms, here):
    """The smoke answer at a place from the day's parsed polygons (_fetch_smoke): the densest one over it."""
    best = None
    for dens, ring in hms["polygons"]:
        if (best is None or SMOKE_RANK[dens] > SMOKE_RANK[best]) and _point_in_ring(here.lon, here.lat, ring):
            best = dens
    return {"checked": True, "detected": best is not None, "density": best, "date": hms["date"]}


def get_smoke_data():
    """True when NOAA HMS mapped smoke over home. False when clear or not checked."""
    return smoke_status()["detected"]


def _fetch_pollen_raw(lat, lon, api_key):
    r = upstream.get(
        "https://pollen.googleapis.com/v1/forecast:lookup", "pollen", counted=True,
        params={
            "key": api_key,
            "location.latitude": lat,
            "location.longitude": lon,
            "days": 2   # today, plus tomorrow for Coming up
        },
        timeout=(2, 5),
    )
    r.raise_for_status()
    return r.json()


def _pollen_types(day):
    out = []
    for ptype in (day or {}).get("pollenTypeInfo", []):
        index_info = ptype.get("indexInfo", {})
        out.append({
            "name": ptype.get("displayName", "Unknown"),
            "level": index_info.get("category", "None"),
            "index": index_info.get("value", 0),
            "in_season": ptype.get("inSeason", False),
        })
    return out


def _fetch_pollen():
    """Today's and tomorrow's pollen, one call: {"today": [...], "tomorrow": [...]}."""
    api_key = env_key("GOOGLE_POLLEN_API_KEY")
    if not api_key:
        return {"today": [], "tomorrow": []}
    # Hard deadline: DNS stalls on pollen.googleapis.com have been observed at ~30s
    # even with requests timeout set — that blocked the whole /api/context response.
    budgets.spend("pollen")   # here, not in the deadline's thread: a paused or spent budget raises to the caller
    data = _call_with_deadline(_fetch_pollen_raw, runtime.HOME_LAT, runtime.HOME_LON, api_key, deadline_s=5, default=None)
    daily = (data or {}).get("dailyInfo") or []
    if not daily:
        budgets.fail("pollen", budgets.POLLEN_PAUSE_S)   # don't ask again for 10 minutes
        raise RuntimeError("Google Pollen did not answer")
    return {"today": _pollen_types(daily[0]), "tomorrow": _pollen_types(daily[1]) if len(daily) > 1 else []}


def _dev_server():
    return os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def _fetch_pollen_kept():
    """_fetch_pollen, kept on disk for the day on the local dev server only."""
    if not _dev_server():
        return _fetch_pollen()
    f = DEV_CACHE_DIR / f"pollen-{local_now():%Y-%m-%d}-{runtime.HOME_LAT:.2f}-{runtime.HOME_LON:.2f}.json"   # the day's, like the cache
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        pass
    value = _fetch_pollen()
    try:
        DEV_CACHE_DIR.mkdir(exist_ok=True)
        f.write_text(json.dumps(value))
    except OSError:
        pass
    return value


def get_pollen_days():
    """{"today": [...], "tomorrow": [...]} from Google, kept for the rest of the home's day: it's a daily forecast, so one
    call a day per running server (plus restarts and the Refresh button), in every season. A new day is a new key."""
    if not place_known():
        return {"today": [], "tomorrow": []}
    try:
        # the key is the day's, so an answer never outlives its day: a day's ttl keeps it until the key changes
        value, _ = cached("pollen:" + local_now().strftime("%Y-%m-%d"), _fetch_pollen_kept, ttl=24 * 3600,
                          keep=24 * 3600, force=_force(), force_floor=600, wait=7)
        return value if isinstance(value, dict) else {"today": value or [], "tomorrow": []}
    except Exception:
        return {"today": [], "tomorrow": []}


def get_pollen_data(lat=None, lon=None):
    """Today's pollen from Google: [{name, level, index, in_season}, ...]."""
    return get_pollen_days()["today"]


# ── Sources that are off or not set up ──────────────────────────────────
# "off": turned off in the config (or no road). "not_set_up": on, but its key is missing. "ok", or "error" when it
# is on and set up and didn't answer. A source that is off or not set up is never fetched.

def source_on(name):
    return runtime.CFG.sources.get(name, True) if runtime.CFG else True


def place_known():
    """True when the home has a place: the sources that look at the home's coordinates wait for one (config.located)."""
    return runtime.HOME_LAT is not None and runtime.HOME_LON is not None


def source_state(name):
    """"needs_place" for a source that looks at the home's coordinates while the home has none, else "off" when it is turned
    off, else "" (on)."""
    if not source_on(name):
        return "off"
    return "" if place_known() else "needs_place"


def pollen_state():
    if not source_on("pollen"):
        return "off"
    if not place_known():
        return "needs_place"
    return "ok" if env_key("GOOGLE_POLLEN_API_KEY") else "not_set_up"


def pollen_live():
    return pollen_state() == "ok"


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def place_rows(rows):
    """A home with one monitor gets only that monitor's rows: the token may see others (a second monitor, an old one),
    and an indoor-only home's outdoor PM2.5 is its source. Explicit ids choose among account monitors.
    The demo and no config: as they came.
    A copy: the cache keeps its list."""
    if not isinstance(rows, list) or runtime.CFG is None or runtime.CFG.demo or runtime.CFG.place is None:
        return rows
    place = runtime.CFG.place
    selected = {kind: getattr(place, kind + "_id") for kind in ("indoor", "outdoor") if getattr(place, kind) is not None}
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        kind = next((k for k, mid in selected.items() if mid is not None and mid == row.get("locationId")), None)
        if kind is None and _portable(row) and (row.get("locationType") not in ("indoor", "outdoor") or any(
                isinstance(r, dict) and not _portable(r) and r.get("locationType") == row.get("locationType") for r in rows)):
            continue   # a Go next to the home's own monitor of that kind, or with no kind; a home whose only indoor or
                       # outdoor monitor is a Go keeps it, as before
        kind = kind or row.get("locationType")
        if kind in ("indoor", "outdoor"):
            if kind not in selected or (selected[kind] is not None and selected[kind] != row.get("locationId")):
                continue
            row = {**row, "locationType": kind}
        out.append(row)
    return out


def _iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _row_time(row):
    try:
        return _iso(str(row["timestamp"]))
    except (KeyError, ValueError):
        return None


def _past(location_id, frm, to, keep=HIST_KEEP):
    """A monitor's cloud readings between frm and to, trimmed to `keep` (the charts' fields unless told otherwise;
    None keeps every field, for the history store, which trims what it doesn't keep)."""
    rows = api_get(f"/locations/{int(location_id)}/measures/past", {
        "from": frm.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": to.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return [{k: r.get(k) for k in (r if keep is None else keep) if r.get(k) is not None}
            for r in rows or [] if isinstance(r, dict)]


def merge_rows(old, new, start):
    """old and new rows by timestamp (a new row replaces an old one at the same time), oldest first, from `start` on."""
    by_time = {}
    for row in list(old) + list(new):
        t = _row_time(row)
        if t is not None and t >= start:
            by_time[t] = row
    return [by_time[t] for t in sorted(by_time)]


def _day_fetch(location_id, key):
    """A monitor's last HIST_DAY hours: the cached day extended with the readings since its newest row (one small
    request), or the whole day when there's nothing cached yet or the cached day is too old to extend."""
    def fetch():
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=HIST_DAY)
        with _cache_lock:
            entry = _cache.get(key)
        old = entry[1] if entry and isinstance(entry[1], list) else []
        times = [t for t in map(_row_time, old) if t is not None]
        newest = max(times) if times else None
        if newest is None or newest < start:
            return _past(location_id, start, now)
        return merge_rows(old, _past(location_id, newest - HIST_OVERLAP, now), start)
    return fetch


def history_rows(location_id, hours, force=False):
    """The last `hours` of readings for one monitor, trimmed to the fields the page and the summary use. Up to a day,
    they're cut from the monitor's cached day (_day_fetch); longer ranges are asked for as a whole."""
    ttl, swr = HIST_TTL.get(hours, (REFRESH_SECONDS, 0))
    if hours <= HIST_DAY:
        key = f"ag:hist:{int(location_id)}:{HIST_DAY}h"
        rows, info = cached(key, _day_fetch(location_id, key), ttl=ttl, swr=swr, keep=3600, force=force,
                            force_floor=REFRESH_FLOOR_SECONDS)
        if hours < HIST_DAY:
            start = datetime.now(timezone.utc) - timedelta(hours=hours)
            rows = [r for r in rows if (_row_time(r) or start) >= start]
        return rows, info

    def fetch():
        now = datetime.now(timezone.utc)
        return _past(location_id, now - timedelta(hours=hours), now)
    return cached(f"ag:hist:{int(location_id)}:{hours}h", fetch, ttl=ttl, swr=swr, keep=3600, force=force,
                  force_floor=REFRESH_FLOOR_SECONDS)


def current_rows(force=False):
    """Every monitor's current reading on the home's token: one request for all of them, at most once per
    REFRESH_SECONDS (the Refresh button: once a minute at most), shared by every tab."""
    return cached("ag:current", lambda: api_get("/locations/measures/current"), ttl=REFRESH_SECONDS, keep=3600,
                  force=force, force_floor=REFRESH_FLOOR_SECONDS)


FILL_DAYS = 7   # d: how far back the cloud fills the home's own history (the cloud's own limit for the chart)
_filling = set()   # serials with a fill running: one fill per monitor at a time
_filling_lock = threading.Lock()
_asked_until = {}   # (serial, cloud id) -> the end of the last range the cloud answered for, in this process


def _fill_one(store, mon):
    """One monitor: the gaps of the last FILL_DAYS days, one /measures/past call spanning them, the rows that fall in a
    gap stored. Nothing to ask when there is no gap. After the first answer, a fill asks only from where the last one
    ended (less GAP, for rows the cloud got late or was still averaging): a gap the cloud had nothing for isn't asked again, so a monitor
    that drops off the Wi-Fi now and then costs a call over its new gap, not the whole week each time."""
    try:
        now = datetime.now(timezone.utc)
        gaps = store.gaps(mon.serial, now - timedelta(days=FILL_DAYS), now)
        floor = _asked_until.get((mon.serial, mon.location_id))
        if floor is not None:
            floor -= history_store.GAP
            gaps = [(max(a, floor), b) for a, b in gaps if b - max(a, floor) >= history_store.CLOUD_ROW]
        if not gaps:
            return
        rows = _past(mon.location_id, gaps[0][0], gaps[-1][1], keep=None)   # every field: the store keeps what it keeps
        added = store.fill(mon.serial, rows, gaps)
        _asked_until[(mon.serial, mon.location_id)] = gaps[-1][1]   # only once stored: a failed write asks the range again
        log.info("history fill: %d gap(s), %d row(s) added", len(gaps), added)
    except Exception as error:   # the cloud being down must not hurt reads; the next trigger tries again
        log.warning("history fill failed (%s)", type(error).__name__)
    finally:
        with _filling_lock:
            _filling.discard(mon.serial)


def start_fill(reason):
    """With a token and a history store, fill the store's gaps from the cloud in the background (one call per monitor
    that has a cloud id). Called at start, when a token is saved and when reads resume after a long gap."""
    store = getattr(runtime, "STORE", None)
    if store is None or not _has_token() or not runtime.CFG or not runtime.CFG.place:
        return
    for mon in config.monitors(runtime.CFG):
        if mon.location_id in localnet.LOCAL_IDS.values():   # no cloud id yet: nothing to ask
            continue
        with _filling_lock:
            if mon.serial in _filling:
                continue
            _filling.add(mon.serial)
        log.info("history fill (%s): %s", reason, mon.kind)
        _REFRESH_POOL.submit(_fill_one, store, mon)


def local_reads_on():
    """True when this copy reads monitors on the Wi-Fi: a real config with at least one serial, not on a hosted copy."""
    cfg = runtime.CFG
    return bool(cfg and not cfg.demo and cfg.place and config.monitors(cfg)
                and localnet.environment_blocker() != "hosted")


_MONITOR_VIA = {}   # kind -> "local" | "cloud" | "stale": where home_rows last read each monitor from, for /health


def _has_token():
    return config.real_value(runtime.TOKEN)


def monitor_states():
    """Where each configured monitor is read from now, by kind: "local", "cloud" or "stale" (no addresses, no serials).
    home_rows' order, worked out on every call from the poller's state (a page needn't have asked for readings): the
    Wi-Fi when its reading is fresh, else the cloud with a token (unless home_rows last found no cloud row for it),
    else not answering."""
    cfg = runtime.CFG
    if not cfg or cfg.demo or not cfg.place:
        return {}
    poller = getattr(runtime, "POLLER", None)
    serial = {m.kind for m in config.monitors(cfg)}
    out = {}
    for kind in config.MONITOR_KINDS:
        if getattr(cfg.place, kind) is None and kind not in serial:
            continue
        if poller is not None and kind in serial and poller.state(kind) == "local":
            out[kind] = "local"
        elif _has_token():
            out[kind] = "stale" if _MONITOR_VIA.get(kind) == "stale" else "cloud"
        else:
            out[kind] = "stale"
    return out


def _last_row(poller, mon):
    """A monitor's last reading: the poller's, or after a restart the history store's (rebuilt to the poller's shape)."""
    row = poller.latest(mon.kind)
    store = getattr(runtime, "STORE", None)
    if row is None and store is not None:
        last = store.last(mon.serial)
        if last is not None:
            row = {**last, "serialno": mon.serial, "locationId": mon.location_id, "locationName": mon.name,
                   "locationType": mon.kind, "via": "local"}
    return row


def home_rows(force=False):
    """The home's monitors' current readings and the cache info, one read order per monitor: the Wi-Fi reading when
    fresh, else the cloud's (with a token), else the last reading marked stale. When every monitor is fresh on the
    Wi-Fi, the cloud isn't asked. Then the indoor-only home's outdoor source, as before. RuntimeError when the cloud
    is needed and fails and nothing else can be shown."""
    poller = getattr(runtime, "POLLER", None)
    mons = {m.kind: m for m in config.monitors(runtime.CFG)} if poller is not None and runtime.CFG else {}
    info = {"state": "hit", "age": 0}
    if not mons:   # no Wi-Fi reads: the cloud's rows as they come
        value, info = current_rows(force=force)
        value = place_rows(value)
    else:
        place = runtime.CFG.place
        wanted = [k for k in config.MONITOR_KINDS if getattr(place, k) is not None or k in mons]
        local = {k for k in mons if poller.state(k) == "local"}
        cloud, error = [], None
        if _has_token() and any(k not in local for k in wanted):
            try:
                raw, info = current_rows(force=force)
                cloud = raw if isinstance(raw, list) else []
            except RuntimeError as e:
                error = e
        by_type = {r.get("locationType"): r for r in place_rows(cloud) if isinstance(r, dict)}
        value = []
        for kind in wanted:
            row, via = None, "stale"
            if kind in local:
                row, via = poller.latest(kind), "local"
            if row is None and kind in mons:
                serial = mons[kind].serial
                row = next((r for r in cloud if isinstance(r, dict) and str(r.get("serialno", "")).lower() == serial), None)
                if row is None and str((by_type.get(kind) or {}).get("serialno") or "").lower() in ("", serial):
                    row = by_type.get(kind)   # the configured id's row, unless it says it's another monitor
                via = "cloud"
            elif row is None:
                row, via = by_type.get(kind), "cloud"
            if row is not None and via == "cloud":
                row = {**row, "via": "cloud", "locationType": kind}
            if row is None and kind in mons:
                row, via = _last_row(poller, mons[kind]), "stale"
                if row is not None:
                    row = {**row, "stale": True}
                    if localnet.environment_blocker() == "container" and not _has_token():
                        row["fix"] = "home_server"   # the address was written at --home-server: only running it again can change it
            if row is not None:
                value.append(row)
                _MONITOR_VIA[kind] = via
            else:
                _MONITOR_VIA[kind] = "stale"
        if error is not None and not value:
            raise error
    src = outdoor_source(with_history=False) if isinstance(value, list) else None
    if src is not None:   # an indoor-only home: its outdoor source stands as the outdoor monitor (a copy: the cache keeps its list)
        value = value + [source_monitor(src)]
    return value, info


def area_monitors(lat, lon):
    """The public monitors within AREA_SEARCH_KM of a point with their PM2.5 now (publicmap.fetch_area), from the
    server's own cache: one map request per place (rounded to about 1 km) every NEARBY_REFRESH_SECONDS at most.
    Raises when the map doesn't answer and nothing recent is cached."""
    lat, lon = round(lat, 2), round(lon, 2)
    value, _ = _shared_cached(f"ag:area:{lat:.2f},{lon:.2f}", lambda: publicmap.fetch_area(lat, lon, AREA_SEARCH_KM),
                              force=_force(), **AREA_CACHE)
    return value or []


def _within_km(rows, here, km):
    """The rows within `km` of a place, nearest first, as (distance km, row)."""
    out = []
    for loc in rows or []:
        lat, lon = (loc.get("latitude"), loc.get("longitude")) if isinstance(loc, dict) else (None, None)
        if lat is not None and lon is not None:
            d = haversine_km(here.lat, here.lon, lat, lon)
            if d < km:
                out.append((d, loc))
    out.sort(key=lambda r: r[0])
    return out


def _fetch_nearby(here=None, rows=None, skip_own=True):
    """The public monitors within NEARBY_RADIUS_KM of a place (the home by default), nearest first, from the area
    request (area_monitors) unless `rows` are given. With skip_own, a monitor within publicmap.OWN_KM of the place is
    the home's own outdoor monitor and isn't listed (the demo's public monitor lists itself first). Each gets its distance, its link on AirGradient's map and pm02_corrected (the map's, or
    the raw pm02 corrected when it has the humidity): the page compares neighbors with a home's own monitors on the
    corrected basis (summary.corrected_pm). The map gives no owner's name for most monitors; a row then has none."""
    here = here or home_here()
    if here.lat is None:
        return []
    rows = area_monitors(here.lat, here.lon) if rows is None else rows
    nearby = []
    for d, loc in _within_km(rows, here, NEARBY_RADIUS_KM):   # copies: the cached list stays as it came
        if skip_own and d < publicmap.OWN_KM:
            continue
        lat, lon = loc["latitude"], loc["longitude"]
        pm = loc.get("pm02_corrected")
        pm = pm if pm is not None else summary_rules.corrected_pm(loc.get("pm02"), loc.get("rhum"))
        link = airgradient_map(round(lat, MAP_LINK_DECIMALS), round(lon, MAP_LINK_DECIMALS))
        nearby.append({**loc, "distance_km": round(d, 1), "map_url": link,
                       **({"pm02_corrected": pm} if pm is not None else {})})
    return nearby


def outdoor_from():
    """The configured outdoor source of an indoor-only home (a public monitor's id, "area" or "model"), or None: a home
    with an outdoor monitor, no config, or the demo (its own seams)."""
    if runtime.CFG is None or runtime.CFG.demo or runtime.CFG.place is None or runtime.CFG.place.outdoor is not None or not place_known():
        return None   # without a place, no public monitor or area estimate can be found
    return runtime.CFG.outdoor_from


def _map_start(key, fetch, **kw):
    """Starts a map fetch through the cache without waiting on it (a value already cached is left as it is)."""
    try:
        cached(key, fetch, wait=0, **kw)
    except SOURCE_ERRORS:   # still running (TimeoutError), or already failed: the wait below answers either way
        pass


def _map_wait(key, fetch, deadline, **kw):
    """The value a _map_start began, waiting no longer than what's left before `deadline` (time.monotonic()), or None
    when the map didn't answer in time (the fetch goes on in the background for the next request)."""
    try:
        value, _ = cached(key, fetch, wait=max(0.0, min(publicmap.MAP_WAIT, deadline - time.monotonic())), **kw)
        return value
    except SOURCE_ERRORS:
        return None


def source_histories(mons, hours, deadline, measures=publicmap.MAP_MEASURES, cache=SOURCE_HIST_CACHE):
    """Each public monitor's last `hours` on AirGradient's map as /api/history rows, oldest first; [] for one without
    a map id or that didn't answer in time. Through the cache, in the request thread, under keys that carry each
    monitor's map id (and the measures, when not all of them): every history is asked at once and the waits share one
    deadline, so five monitors take about as long as one."""
    tag = "" if measures is publicmap.MAP_MEASURES else ":" + ",".join(measures)
    hist_jobs = []
    for m in mons:
        mid = m.get("mapId")
        hist_jobs.append(None if not isinstance(mid, int) else (f"src:maphist:{mid}:{hours}h{tag}",
                                                                lambda mid=mid: publicmap.history(mid, hours, measures=measures)))
    for job in filter(None, hist_jobs):
        _map_start(*job, **cache)
    return [[dict(r) for r in (_map_wait(*job, deadline, **cache) or [])] if job else [] for job in hist_jobs]


def source_history(mon, hours, deadline, measures=publicmap.MAP_MEASURES, cache=SOURCE_HIST_CACHE):
    """One public monitor's source_histories."""
    return source_histories([mon], hours, deadline, measures, cache)[0]


def _row_points(rows, now):
    """/api/history rows as (local time, row), oldest first, like history_points."""
    points = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).astimezone(now.tzinfo)
        except (KeyError, ValueError, TypeError):
            continue
        points.append((ts, row))
    return sorted(points, key=lambda p: p[0])


def model_hours(now):
    """The air-quality model's outdoor PM2.5 as (local time, value), oldest first."""
    return sorted((_local(t, now.tzinfo), v) for t, v in (get_air_forecast() or {}).items())


def _source(kind, **facts):
    return {"kind": kind, "id": None, "name": None, "street": None, "contributor": None, "url": None,
            "distance_km": None, "row": None, "history": [], "area_count": 0, "area_factor": None, "fallback": False,
            **facts}


def _monitor_facts(mon):
    """A public monitor's facts for the page: who and where (its owner's credit), and how far from home."""
    return {"id": mon.get("locationId"), "name": publicmap.name_of(mon), "street": mon.get("publicPlaceName"),
            "contributor": mon.get("publicContributorName"), "url": mon.get("publicPlaceUrl"),
            "distance_km": round(publicmap.km_between(runtime.HOME_LAT, runtime.HOME_LON, mon["latitude"], mon["longitude"]), 1)}


def public_monitor(map_id, deadline):
    """The public monitor with this map id as a row, with its reading now: its name and place (PLACE_CACHE, a day) and
    its measures (every REFRESH_SECONDS, like an own monitor), both asked at once. {} when the map has no such monitor
    (gone, or not an AirGradient one), None when it didn't answer in time."""
    place_job = (f"src:place:{map_id}", lambda: publicmap.fetch_place(map_id))
    now_job = (f"src:now:{map_id}", lambda: publicmap.fetch_now(map_id))
    now_cache = {"ttl": REFRESH_SECONDS, "keep": 3600, "force": _force(), "force_floor": REFRESH_FLOOR_SECONDS}
    _map_start(*place_job, **PLACE_CACHE)
    _map_start(*now_job, **now_cache)
    place = _map_wait(*place_job, deadline, **PLACE_CACHE)
    now = _map_wait(*now_job, deadline, **now_cache)
    if place is None:
        return None
    try:
        return publicmap.monitor_row(place, now) if place else {}
    except RuntimeError:
        return {}


def _area_source(now, deadline):
    """The area estimate: the nearest AREA_MAX_MONITORS reporting outdoor monitors within AREA_KM, each one's hourly
    PM2.5 over the last day, their median per hour, and its median ratio to the model (calibrate_forecast's factor:
    the median of a per-hour median ratio is the ratio of the per-hour median, as the ratio is linear in the value).
    The model instead with fewer than AREA_MIN_MONITORS with history, or too few hours to compare."""
    # the area request lists the monitors around with their reading now; the reporting ones are kept (publicmap.within)
    try:
        rows = area_monitors(runtime.HOME_LAT, runtime.HOME_LON)
    except SOURCE_ERRORS:
        return _source("model")
    near = [m for _, m in publicmap.within(rows, runtime.HOME_LAT, runtime.HOME_LON, publicmap.AREA_KM, now)][:AREA_MAX_MONITORS]
    series = []
    for rows in source_histories(near, SOURCE_HOURS, deadline, measures=publicmap.PM_ONLY):
        hourly = {}
        for t, row in _row_points(rows, now):
            v = fnum(row.get("pm02_corrected"))
            if v is not None and now - timedelta(hours=SOURCE_HOURS) <= t <= now:
                hourly.setdefault(t.replace(minute=0, second=0, microsecond=0), []).append(v)
        if hourly:
            series.append({h: sum(vs) / len(vs) for h, vs in hourly.items()})
    if len(series) < publicmap.AREA_MIN_MONITORS:
        return _source("model")
    area = []
    for h in sorted({h for s in series for h in s}):
        vals = sorted(s[h] for s in series if h in s)
        mid = len(vals) // 2
        area.append((h, round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)))
    _, k = summary_rules.calibrate_forecast(model_hours(now), area, now)
    if k is None:
        return _source("model")
    return _source("area", area_count=len(series), area_factor=k,
                   history=[{"timestamp": h.isoformat(), "pm02_corrected": v} for h, v in area])


def outdoor_source(now=None, with_history=True):
    """Where an indoor-only home's outdoor PM2.5 comes from now, or None for a home with an outdoor monitor:
    {"kind": "monitor"|"area"|"model", "id", "name", "street", "contributor", "url", "distance_km" (the chosen public
    monitor's, kept when it falls back), "row" (its reading, with pm02_corrected; kind monitor only), "history" (its
    /api/history rows over SOURCE_HOURS, or the area's hourly medians), "area_count" (area monitors used),
    "area_factor" (the area's ratio to the model), "fallback" (why the chosen monitor's reading isn't used: "monitor",
    it isn't reporting; "list", AirGradient's map didn't answer, so whether it reports is unknown; else False)}.
    with_history=False skips a public monitor's own history (/api/current shows only its reading); the area still reads
    its monitors', as they decide whether it is "area" or "model".
    Facts only: the page and phrases.py word them. Never raises for an upstream: monitor → area → model."""
    of = outdoor_from()
    if of is None:
        return None
    if of == "model":
        return _source("model")
    now = now or local_now()
    deadline = time.monotonic() + SOURCE_BUDGET   # the monitors' readings now and their histories, in all
    about, fallback = {}, False
    if isinstance(of, int):   # a public monitor's id on AirGradient's map, chosen at setup: read alone, never searched
        mon = public_monitor(of, deadline)
        if mon:
            _SOURCE_SEEN[of] = mon
        known = mon or _SOURCE_SEEN.get(of)
        about = _monitor_facts(known) if known else {"id": of}
        if mon and outdoor_current(mon, now):
            return _source("monitor", **about, row=publicmap.reading_row(mon),
                           history=source_history(mon, SOURCE_HOURS, deadline) if with_history else [])
        fallback = "list" if mon is None else "monitor"
    src = _area_source(now, deadline)
    return {**src, **about, "fallback": fallback}


def source_facts(src):
    """What the page and the read get about a source: SOURCE_FACTS, without its row and history."""
    return {k: src[k] for k in SOURCE_FACTS}


def source_monitor(src):
    """The source as /api/current's outdoor monitor: a public monitor's reading row, or (area, model) an outdoor entry
    without readings, so the page knows what to draw. Each says where it comes from ("source")."""
    if src["kind"] == "monitor":
        return {**src["row"], "source": source_facts(src)}
    return {"locationType": "outdoor", "source": source_facts(src)}


def source_history_rows(location_id, frm, to):
    """The chosen public monitor's rows between frm and to (UTC datetimes) from AirGradient's map, for /api/history:
    from the shortest History range (HIST_TTL's hours) that reaches back to frm, the longest at most. [] for any other
    id, or when the map doesn't answer."""
    if location_id != outdoor_from() or not isinstance(location_id, int):
        return []
    mon = {"locationId": location_id, "mapId": location_id}
    ranges = sorted(HIST_TTL)
    back = (datetime.now(timezone.utc) - frm - HIST_SLACK).total_seconds() / 3600
    hours = next((h for h in ranges if h >= back), ranges[-1])
    rows = source_history(mon, hours, time.monotonic() + SOURCE_BUDGET, cache=CHART_HIST_CACHE)
    out = []
    for r in rows:
        try:
            t = _iso(str(r["timestamp"]))
        except (KeyError, ValueError):
            continue
        if frm <= t <= to:
            out.append(r)
    return out


def fnum(value):
    """Float or None. Zero is a real reading."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def monitor_for_id(location_id):
    """The configured monitor (with a serial) behind a history id: the one with that cloud id, else the one with the
    fixed local id (localnet.LOCAL_IDS), else None."""
    mons = config.monitors(runtime.CFG) if runtime.CFG else []
    cloud = next((m for m in mons if m.location_id == location_id and m.location_id not in localnet.LOCAL_IDS.values()), None)
    return cloud or next((m for m in mons if m.location_id == location_id), None)


def _cloud_tail(location_id, since):
    """A monitor's cloud readings, from the ranges the charts already cache: its last day when `since` is within it,
    else its week. RuntimeError when the cloud doesn't answer."""
    hours = HIST_DAY if datetime.now(timezone.utc) - since <= timedelta(hours=HIST_DAY) else 168
    rows, _ = history_rows(location_id, hours)
    return rows


def store_rows(location_id, frm, to):
    """The home's own history of a monitor between frm and to (aware datetimes), oldest first, as stored (every measured
    field); None when this copy keeps no history of that monitor, so the caller asks the cloud as before.
    The store answers up to its last reading. When reads have stopped for longer than a gap (the monitor left the
    Wi-Fi, the app was stopped), the time after it comes from the cloud with a token and a cloud id; without them, or
    when the cloud doesn't answer, the rows end at the last reading: never an invented point."""
    store = getattr(runtime, "STORE", None)
    mon = monitor_for_id(location_id) if store is not None else None
    last = store.last(mon.serial) if mon is not None else None
    if last is None:
        return None
    rows = store.rows(mon.serial, frm, to)
    end = max(_row_time(last), frm)
    if to - end <= history_store.GAP or not _has_token() or mon.location_id in localnet.LOCAL_IDS.values():
        return rows
    try:
        tail = _cloud_tail(mon.location_id, end)
    except RuntimeError:
        return rows
    later = sorted(((t, r) for r in tail if isinstance(r, dict) and (t := _row_time(r)) is not None and end < t <= to),
                   key=lambda p: p[0])
    return rows + [r for _, r in later]


def history_points(location_id, now_local, hours=24):
    if not location_id:
        return []
    try:
        rows = store_rows(location_id, datetime.now(timezone.utc) - timedelta(hours=hours), datetime.now(timezone.utc))
        if rows is None:
            rows, _ = history_rows(location_id, hours)
    except (RuntimeError, ValueError, TypeError):
        return []
    points = []
    for row in rows or []:
        try:
            ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).astimezone(now_local.tzinfo)
        except (KeyError, ValueError, AttributeError, TypeError):
            continue
        points.append((ts, row))
    points.sort(key=lambda p: p[0])
    return points


def pm_of(r):
    return fnum(r.get("pm02_corrected", r.get("pm02")))


def _local(ts, tz):
    """'2026-11-08T16:00' (Open-Meteo, local) as an aware datetime."""
    return datetime.fromisoformat(ts).replace(tzinfo=tz)


def outdoor_current(row, now):
    """Whether the outdoor reading counts as a reading now: no row to judge by, or online and under
    publicmap.STALE_READING old."""
    return row is None or publicmap.reporting(row, now)   # the same rule as check_config.py's
