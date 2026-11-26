"""Prepare private files, discover monitors, write household choices and verify them.

Run with no answers to prepare .env; with --address and --profiles to finish setup.
The railway command transfers saved settings through stdin. --live checks a running page.
--status lists what's done and the next command; it changes nothing.
--agent on links the Home Air skill into the agent's skills folders; off removes only those links.
"""
import argparse
import base64
import contextlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_config
import config
import localnet
import phrases
import publicmap
import road_data
import sources  # installs the request-log filter that hides query credentials
import token_setup
from config import (ENV_FILE_MODE, PLACE_DECIMALS, PROFILES, ConfigError, defines,
                    env_key, load_config, parse_dotenv, public_view, write_env_key)

from road_data import EXACT_DECIMALS
from upstream import USER_AGENT  # Nominatim requires an identifying User-Agent: the app's, HomeAir/<version>.
# HOMEAIR_LOOKUP_BASE (tests and rehearse only, like AIRGRADIENT_API_BASE): one local server answering both lookups.
LOOKUP_BASE = os.environ.get("HOMEAIR_LOOKUP_BASE", "").rstrip("/")
NOMINATIM_SEARCH = f"{LOOKUP_BASE}/search" if LOOKUP_BASE else "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = f"{LOOKUP_BASE}/reverse" if LOOKUP_BASE else "https://nominatim.openstreetmap.org/reverse"
OPEN_METEO_FORECAST = f"{LOOKUP_BASE}/v1/forecast" if LOOKUP_BASE else "https://api.open-meteo.com/v1/forecast"  # timezone=auto resolves a point's timezone.
LOOKUP_RESULTS = 3  # matches: enough to catch a name shared by different states.
AMBIGUOUS_SHOWN = 3  # matches shown when a place needs its city or state.
LOOKUP_TIMEOUT = 15  # seconds per place or timezone request.
PLACE_HALF_CELL_DEG = 10 ** -PLACE_DECIMALS / 2  # degrees: half the rounded place's 0.01° cell, for its worst-case shift
PUBLIC_AREA_MARGIN_KM = 1  # km: keep the existing public-map box margin beyond AREA_KM
PUBLIC_ROUNDING_MARGIN_KM = 0.05  # km: extra beyond the half-cell diagonal for box precision and latitude variation
LIVE_RETRY_SECONDS = 10  # --live keeps trying this long when the page doesn't answer yet: an app started a moment ago (a Codex run checked 5 times too early).
LIVE_TIMEOUT = 30  # seconds: a running page may be waiting on its outside sources.
DEFAULT_PORT = 5555  # server.py's local port without PORT.
AREA_CATEGORIES = ("place", "boundary")  # Nominatim's area names, rather than buildings or streets.
DISTRICT_KEYS = ("suburb", "city_district", "borough", "quarter")  # district before city for a street lookup.
CITY_KEYS = ("city", "town", "village", "hamlet")
NORTHEAST = {"CT": "Connecticut", "ME": "Maine", "MA": "Massachusetts", "NH": "New Hampshire", "RI": "Rhode Island",
             "VT": "Vermont", "NY": "New York", "NJ": "New Jersey", "PA": "Pennsylvania"}  # normalize known state codes.
NOT_FOUND = "Couldn't find that address. Try a nearby landmark with its city, or pass --lat and --lon from a map app."
KEY_NAMES = ("AIRGRADIENT_TOKEN", "GOOGLE_POLLEN_API_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY")  # the only keys transferred to Railway.
STATUS_TIMEOUT = 15  # seconds for --status's one readings request: AirGradient took 7 s to connect on 2026-11-13, and 5 s read that as a refused token.
RUN = "scripts/run.sh"  # how ./homeair runs every script: uv, else installing uv, else Python 3.11+ (the uv ladder).
SERVER_CMD = f"{RUN} server.py"
# The commands messages name: ./homeair's short forms (the file at the repo root maps each to this script's flags).
CLI = "./homeair"
SETUP_CMD = f"{CLI} setup"
START_CMD = f"{CLI} start"  # starts it in the background, so it outlives the agent's session, and waits for the address; the log is in app.log
HOME_CMD = f'{SETUP_CMD} --address "<neighborhood or address>" --profiles <toddler,sinus,runner or none>'
STATUS_CMD = f"{CLI} status"
CHECK_CMD = f"{CLI} check"  # check_config.py, then --live
WAIT_TOKEN_CMD = f"{CLI} connect --wait"
CONNECT_CMD = f"{CLI} connect"  # prints the token form's address
LOGIN_ON_CMD = f"{CLI} start-at-login on"
LOGIN_OFF_CMD = f"{CLI} start-at-login off"
MONITOR_CMD = f"{CLI} monitors use"  # then a serial
WAIT_TOKEN_SECONDS = 90  # one wait: under the shortest default command limit we know (Claude Code stops a command after 120 s).
PORT_FILE = "app.port"  # the port --start chose, in the repo folder (ignored by git)
FALLBACK_PORT = 5600  # the first port --start tries when the usual one is taken.
FALLBACK_TRIES = 20  # ports tried from FALLBACK_PORT.
STARTUP_WAIT = 30  # seconds for the page to answer after the system starts it at login (uv may install packages first).
OPEN_TIMEOUT = 10  # seconds the browser opener gets: it returns at once, or something is wrong.
WAIT_POLL_SECONDS = 2  # how often --wait-token looks for the saved token: the user sees the agent move on within 2 s.
URL_FILE = "agent-url"  # Home Air's address the skill reads instead of this computer's (skill add --url), in the repo folder (ignored by git)
SKILL = "home-air-quality"  # the reading skill: its folder under skills/ here, and its name in each agent's skills folder
AGENT_FILE = "agent-links.json"  # what --agent on linked, in the repo folder (ignored by git), so --agent off removes only those
SHARED_SKILLS = ".agents/skills"  # under the home folder: the shared folder Codex, Cursor and Gemini CLI read
CLAUDE_SKILLS = ".claude/skills"  # under the home folder: Claude Code's own skills folder (it doesn't read the shared one)
AGENT_ON_CMD = f"{CLI} skill add"
AGENT_OFF_CMD = f"{CLI} skill remove"
CODEX_VARS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID")  # set by Codex for its commands; not CODEX_HOME, a user setting
FOR_AGENTS = {SHARED_SKILLS: ("Codex", "Cursor", "Gemini CLI"), CLAUDE_SKILLS: ("Claude Code",)}  # who reads each skills folder
# The coding agents setup looks for on this computer, to name the ones that get the skill: (name, its folder under the
# home folder, its commands). Either one is enough: a folder alone (an agent used before) or a command on the PATH.
AGENT_SIGNS = (("Claude Code", ".claude", ("claude",)), ("Codex", ".codex", ("codex",)),
               ("Cursor", ".cursor", ("cursor", "cursor-agent")), ("Gemini CLI", ".gemini", ("gemini",)))


class SetupError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def http_get_json(url, params):
    """One GET with the app's User-Agent. Raises on no answer or an HTTP error."""
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=LOOKUP_TIMEOUT)
    r.raise_for_status()
    return r.json()


@dataclass
class Place:
    lat: float
    lon: float
    name: str | None   # None: OpenStreetMap gave no area or city; resolve_place asks for --name
    found_by: str
    words: str   # what was found, for the user to catch a wrong match: no house number, no street
    country: str | None = None   # ISO code, upper case
    state: str | None = None   # a US state's code for the northeast, else its name
    zip: str | None = None
    region: str | None = None
    timezone: str | None = None


def _uniq(parts):
    out = []
    for p in parts:
        p = str(p or "").strip()
        if p and p.casefold() not in (o.casefold() for o in out):
            out.append(p)
    return out


def _us_state(code_or_name):
    """A US state as its two-letter code when it's in NORTHEAST, else as given."""
    s = str(code_or_name or "").strip()
    if s.upper().startswith("US-"):
        s = s[3:]
    for code, name in NORTHEAST.items():
        if s.upper() == code or s.casefold() == name.casefold():
            return code
    return s or None


def _zip5(code):
    m = re.match(r"^(\d{5})(-\d{4})?$", str(code or "").strip())
    return m.group(1) if m else None


def _osm_city(a):
    return next((a[k] for k in CITY_KEYS if a.get(k)), None)


def _osm_state(a):
    return a.get("ISO3166-2-lvl4") or a.get("state")


def _osm_words(r):
    a = r.get("address") or {}
    return ", ".join(_uniq([r.get("name"), a.get("neighbourhood"), *(a.get(k) for k in DISTRICT_KEYS), _osm_city(a),
                            a.get("state"), str(a.get("country_code") or "").upper()]))


def _first_words(text):
    return text.split(",")[0].strip()


def osm_name(results, text):
    """Decided 4: the user's own words when they named an area ("Logan Square"); after a street address or a building,
    OpenStreetMap's district or suburb, else the city, never its smallest area."""
    first = _first_words(text)
    for r in results if first else []:
        cat = r.get("category") or r.get("class")
        if cat in AREA_CATEGORIES and str(r.get("name") or "").casefold() == first.casefold():
            return r["name"]
    a = results[0].get("address") or {}
    for keys in (DISTRICT_KEYS, CITY_KEYS, ("neighbourhood", "county")):
        found = next((a[k] for k in keys if a.get(k)), None)
        if found:
            return found
    return None   # never the user's own text: it could be a street address, which would end up in config.toml


def place_from_osm(results, text, found_by="OpenStreetMap"):
    groups = []
    for r in results:
        a = r.get("address") or {}
        g = (str(a.get("country_code") or "").upper(), str(_osm_state(a) or ""))
        if g not in [x[0] for x in groups]:
            groups.append((g, r))
    if len(groups) > 1:
        raise SetupError(ambiguous([_osm_words(r) for _, r in groups]))
    r, a = results[0], results[0].get("address") or {}
    country = str(a.get("country_code") or "").upper() or None
    return Place(lat=round(float(r["lat"]), EXACT_DECIMALS), lon=round(float(r["lon"]), EXACT_DECIMALS),
                 name=osm_name(results, text) if text else osm_name([r], ""), found_by=found_by, words=_osm_words(r),
                 country=country, state=_us_state(_osm_state(a)) if country == "US" else None,
                 zip=_zip5(a.get("postcode")) if country == "US" else None, region=_osm_city(a))


def ambiguous(words):
    shown = "\n".join(f"  {i}) {w}" for i, w in enumerate(words[:AMBIGUOUS_SHOWN], 1))
    return (f"Several places match, in different states or countries:\n{shown}\n"
            "Ask the user which one, then add its city or state to --address (e.g. \"Logan Square, Chicago\").")


def find_address(text, get=None):
    get = get or http_get_json
    try:
        results = get(NOMINATIM_SEARCH, {"q": text, "format": "jsonv2", "addressdetails": 1,
                                         "limit": LOOKUP_RESULTS, "accept-language": "en"})
    except (requests.RequestException, ValueError):
        raise SetupError("Couldn't reach OpenStreetMap. Check the internet connection and run the command again.") from None
    results = [r for r in results if isinstance(r, dict) and r.get("lat") and r.get("lon")] if isinstance(results, list) else []
    if not results:
        raise SetupError(NOT_FOUND)
    return place_from_osm(results, text)


def find_coordinates(lat, lon, get=None):
    """The area around coordinates the user gave (one Nominatim reverse lookup), for the name, country and ZIP."""
    get = get or http_get_json
    try:
        r = get(NOMINATIM_REVERSE, {"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1, "accept-language": "en"})
    except (requests.RequestException, ValueError):
        r = None
    if not isinstance(r, dict) or not r.get("address"):
        raise SetupError("Couldn't look up the area around these coordinates (OpenStreetMap didn't answer). "
                         "Run the command again, or pass --address.")
    r = {**r, "lat": lat, "lon": lon, "name": ""}
    return place_from_osm([r], "", found_by="OpenStreetMap, from your coordinates")


def valid_timezone(name):
    try:
        ZoneInfo(str(name))
        return bool(name)
    except (ValueError, ZoneInfoNotFoundError):
        return False


def timezone_at(lat, lon, get=None):
    get = get or http_get_json
    try:
        tz = get(OPEN_METEO_FORECAST, {"latitude": round(lat, PLACE_DECIMALS), "longitude": round(lon, PLACE_DECIMALS),
                                      "timezone": "auto", "forecast_days": 1}).get("timezone")
    except (requests.RequestException, ValueError, AttributeError):
        tz = None
    if not valid_timezone(tz):
        raise SetupError("Couldn't get the timezone from Open-Meteo. Run the command again, or pass --timezone "
                         "(an IANA name, like America/Chicago).")
    return tz



def resolve_place(args, get=None):
    if (args.lat is None) != (args.lon is None):
        raise SetupError("--lat and --lon go together.", 2)
    if args.lat is not None and not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180):
        raise SetupError("--lat is between -90 and 90, --lon between -180 and 180.", 2)
    if args.address:
        place = find_address(args.address.strip(), get)
        if args.lat is not None:   # the user's coordinates win over the lookup's
            place.lat, place.lon = round(args.lat, EXACT_DECIMALS), round(args.lon, EXACT_DECIMALS)
            place.found_by += "; coordinates yours"
    else:
        place = find_coordinates(round(args.lat, EXACT_DECIMALS), round(args.lon, EXACT_DECIMALS), get)
    if args.timezone:
        if not valid_timezone(args.timezone):
            raise SetupError(f'--timezone "{args.timezone}" isn\'t a timezone name, like America/Chicago.', 2)
        place.timezone = args.timezone
    elif not place.timezone:
        place.timezone = timezone_at(place.lat, place.lon, get)
    if args.name is not None:
        place.name = clean_text(args.name, "--name")
    if not place.name:
        raise SetupError("The lookup found no area or city to call the home. Pass --name with what the page should call it "
                         '(like "Logan Square"); it is not taken from the address, which could be a street.')
    if args.region is not None:
        place.region = " ".join(args.region.split()) or None
    if args.zip is not None:
        if place.country not in (None, "US"):
            raise SetupError("--zip is for US homes: it links to pollen.com, which covers the US only.", 2)
        if not _zip5(args.zip):
            raise SetupError(f'--zip "{args.zip}": a US ZIP code has 5 digits.', 2)
        place.zip = _zip5(args.zip)
    return place


def clean_text(value, flag):
    text = " ".join(str(value).split())
    if not text:
        raise SetupError(f"{flag} is empty.", 2)
    return text


def saved_keys(root):
    try:
        return parse_dotenv((root / ".env").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def prepare_env(root):
    path = root / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    if not any(defines(line, "AIRGRADIENT_TOKEN") for line in text.splitlines()):
        write_env_key(path, "AIRGRADIENT_TOKEN", "")
    path.chmod(ENV_FILE_MODE)
    ensure_push_keys(root)


def ensure_push_keys(root):
    """Make the phone alerts' VAPID pair once. The private key is its 32 raw bytes and the public key the uncompressed
    point, both base64url on one line: the forms pywebpush and the browser's applicationServerKey take directly."""
    path = root / ".env"
    keys = saved_keys(root)
    if env_key("VAPID_PRIVATE_KEY", keys) and env_key("VAPID_PUBLIC_KEY", keys):
        return
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    write_env_key(path, "VAPID_PRIVATE_KEY", b64(private))
    write_env_key(path, "VAPID_PUBLIC_KEY", b64(public))
    path.chmod(ENV_FILE_MODE)
    print("Alert keys ready")


class AirGradientSlow(SetupError):
    """AirGradient didn't answer in time (or couldn't be reached): trying again is the fix."""


class TokenRefused(SetupError):
    """AirGradient answered and refused the token: entering it again is the fix."""


def account_rows(token, timeout=LOOKUP_TIMEOUT):
    try:
        response = requests.get(f"{sources.API_BASE}/locations/measures/current", params={"token": token},
                                timeout=timeout, allow_redirects=False, headers=sources.upstream.headers())
        if response.status_code in (401, 403):
            raise TokenRefused("AirGradient refused the token. Enter it again on the token page, then try again.")
        response.raise_for_status()
        rows = response.json()
        if response.status_code != 200 or not isinstance(rows, list):
            raise ValueError("Unexpected response")
        return [row for row in rows if isinstance(row, dict)]
    except (requests.Timeout, requests.ConnectionError):
        raise AirGradientSlow(f"AirGradient didn't answer within {timeout} s. Check the internet connection, then try again.") from None
    except (requests.RequestException, ValueError):
        raise SetupError("Couldn't read the monitors from AirGradient. Check the token in .env and API access, then try again.") from None


def wait_token(env, root):
    """Until the token page saves the token (or the wait runs out): then what the account has, so the user needn't say it."""
    end = time.monotonic() + WAIT_TOKEN_SECONDS
    while True:
        token = env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root))
        if token:
            print(connected_line(account_rows(token)))
            return 0
        if time.monotonic() >= end:
            print(f"Not saved yet. Run {WAIT_TOKEN_CMD} again to keep waiting, or ask the user to type done when the page says Connected.")
            return 0
        time.sleep(WAIT_POLL_SECONDS)


def connected_line(rows):
    """One line for the user: the account's monitors by name and kind."""
    if not rows:
        return "Connected to AirGradient, but the account has no monitor with readings yet."
    names = [f'{publicmap.plain_text(r.get("locationName") or "") or "Unnamed monitor"} ({sources.monitor_kind(r) or "kind not set"})' for r in rows]
    return f"Connected to AirGradient: {len(rows)} monitor{'s' if len(rows) != 1 else ''}: {', '.join(names)}."


def choose_monitors(rows, args):
    """The account's monitors by kind: sources.choose_monitors' rule, its refusal as a setup error."""
    try:
        return sources.choose_monitors(rows, args.indoor_id, args.outdoor_id)
    except sources.MonitorChoice as error:
        raise SetupError(str(error)) from None


def outdoor_source(place):
    """An indoor-only home's outdoor source, chosen once: the nearest public monitor reporting within
    publicmap.NEARBY_SOURCE_KM (its id on AirGradient's map, saved as outdoor_from; the app then reads that one monitor
    alone), "area" or "model". One request around the rounded place, widened by half a cell's diagonal (at most about
    0.8 km) plus a margin. The exact place stays on this computer for distance ranking."""
    try:
        lat, lon = round(place.lat, PLACE_DECIMALS), round(place.lon, PLACE_DECIMALS)
        rounding_km = publicmap.KM_PER_DEG * PLACE_HALF_CELL_DEG * math.hypot(1, math.cos(math.radians(lat)))
        world = publicmap.fetch_area(lat, lon, publicmap.AREA_KM + PUBLIC_AREA_MARGIN_KM + rounding_km + PUBLIC_ROUNDING_MARGIN_KM)
        # The extra fetch margin protects coverage, not eligibility: preserve the old exact-centred box locally.
        bounds = publicmap.box(place.lat, place.lon, publicmap.AREA_KM + PUBLIC_AREA_MARGIN_KM)
        world = [m for m in world if bounds["ymin"] <= m["latitude"] <= bounds["ymax"]
                 and bounds["xmin"] <= m["longitude"] <= bounds["xmax"]]
        pick, _, _ = publicmap.suggest(world, place.lat, place.lon, datetime.now(timezone.utc))
        return pick
    except Exception:  # optional public discovery must never block household setup
        print("AirGradient's public map didn't answer. Outdoor air will be an estimate from Open-Meteo's forecast.")
        return "model"


def temp_unit(place):
    """°F for a home in the US, °C elsewhere (decision 8): the unit the page starts in; a viewer can still switch."""
    return "F" if place.country == "US" else "C"


def build_config(place, monitors, profiles, outdoor):
    values = {"name": place.name, "lat": place.lat, "lon": place.lon, "timezone": place.timezone}
    for kind, row in monitors.items():
        values[kind] = publicmap.plain_text(row.get("locationName") or kind)
        values[kind + "_id"] = row["locationId"]
    if place.country == "US" and place.zip:
        values["pollen_zip"] = place.zip
    if place.region:
        values["region"] = place.region
    lines = ["[place]", *(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in values.items()),
             "", "[profiles]", *(f"{key} = {str(key in profiles).lower()}" for key in PROFILES),
             ]
    lines += ["", "[home]", f"temp_unit = {json.dumps(temp_unit(place))}"]
    if outdoor is not None or place.country != "US":
        lines += ["", "[sources]"]
        if outdoor is not None:
            lines.append(f"outdoor_from = {json.dumps(outdoor)}")
        if place.country != "US":
            lines.append("smoke = false")
    return "\n".join(lines) + "\n"


def _reads(text):
    try:
        tomllib.loads(text)
        return True
    except tomllib.TOMLDecodeError:
        return False


def update_config(old, place, monitors, profiles, outdoor, profiles_asked):
    """The config.toml text `old` with what a place with a token owns changed in place: the place, the chosen monitors
    (and a home-server copy's serial and host when it is the same monitor), the outdoor source and the unit when none
    is set, and the profiles only when asked for or absent. Every other line, the roads and the comments stay."""
    raw = tomllib.loads(old)
    values = {"name": place.name, "lat": place.lat, "lon": place.lon, "timezone": place.timezone}
    for kind, row in monitors.items():
        values[kind] = publicmap.plain_text(row.get("locationName") or kind)
        values[kind + "_id"] = row["locationId"]
    if place.country == "US" and place.zip:
        values["pollen_zip"] = place.zip
    if place.region:
        values["region"] = place.region
    text = config.set_keys(old, "place", values)
    kept = kept_monitor_keys(old, monitors)
    drop = {"place": {k: None for k in ("pollen_zip", "region") if k not in values}}
    for kind in KINDS:
        if kind in monitors:
            drop[kind] = {f"{kind}_serial": None, f"{kind}_host": None, **kept.get(kind, {})}
        else:
            drop[kind] = {kind: None, f"{kind}_id": None, f"{kind}_serial": None, f"{kind}_host": None}
    text = config.set_monitor_keys(text, drop)
    if profiles_asked or "profiles" not in raw:
        text = config.set_keys(text, "profiles", {key: key in profiles for key in PROFILES})
    if "temp_unit" not in (raw.get("home") or {}):
        text = config.set_keys(text, "home", {"temp_unit": temp_unit(place)})
    if outdoor is not None:
        text = config.set_keys(text, "sources", {"outdoor_from": outdoor})
    if place.country and place.country != "US":
        text = config.set_keys(text, "sources", {"smoke": False})
    return text


def kept_monitor_keys(old, monitors):
    """What the rebuilt config.toml keeps of the old one's monitors, for config.set_monitor_keys: per kind its serial
    (found on the Wi-Fi) and a home-server copy's host, when the account's monitor chosen for that kind is that one (the
    same serial, or no serial in the account's row and the same id). {} when there is no old file or it can't be read."""
    try:
        place = tomllib.loads(old).get("place", {}) if old else {}
    except tomllib.TOMLDecodeError:
        return {}   # replaced whole, as before: a backup keeps it
    out = {}
    for kind, row in monitors.items():
        serial = str(place.get(f"{kind}_serial") or "").strip().lower()
        cloud = str(row.get("serialno") or "").strip().lower()
        if serial and (cloud == serial or (not cloud and place.get(f"{kind}_id") == row.get("locationId"))):
            out[kind] = {f"{kind}_serial": serial, f"{kind}_host": place.get(f"{kind}_host") or None}
    return out


READ_TRIES = 3          # reads of a found monitor before setup says it didn't answer: another program may be reading it (24 Nov)
READ_RETRY_WAIT = 1.0   # s between those reads; a monitor answers one request at a time


NO_MONITOR = {None: "No AirGradient monitor answered on this network",
              "wsl": "WSL can't find the monitors on your Wi-Fi",
              "container": "a container can't find the monitors on your Wi-Fi",
              "hosted": "Home Air online can't find the monitors on your Wi-Fi"}   # by localnet.environment_blocker(): why nothing was found
KINDS = ("indoor", "outdoor")
HOME_SERVER_CMD = f"{CLI} home-server"


def also_found_line(kind, item):
    """The line naming a monitor of a kind already read, and the command that reads it instead."""
    return (f"Also found: another {kind} monitor (…{item.serial[-4:]}, {item.model}). "
            f"To read it instead: {MONITOR_CMD} {item.serial}")


def found_monitors(found, reader=None, keep=()):
    """The monitors setup will read: per kind one readable monitor (a read at its address answers as that serial, with
    a compensated PM2.5), and the lines to print. The pick never depends on which answered first: a serial in `keep`
    (config.toml's) first, then the lowest serial. Each other one of a kind gets a line with the command that reads it
    instead; a monitor setup can't read says why in one line. A kept serial that doesn't answer this once holds its kind:
    the other monitors are named, never written in its place."""
    reader = reader or localnet.read_current
    keep = set(keep)
    lines, chosen, extra, held = [], {}, [], set()
    for item in sorted(found, key=lambda f: (f.serial not in keep, f.serial)):
        why = localnet.not_readable(item)
        if why:
            lines.append(why)
            continue
        kind = localnet.kind_of(item.model)
        if kind in chosen or kind in held:
            extra.append((kind, item))
            continue
        mon = localnet.Monitor(kind, item.serial, "", localnet.LOCAL_IDS[kind], None)
        row = None
        for attempt in range(READ_TRIES):
            try:
                row = localnet.map_reading(reader(None, item.address, item.port), mon, datetime.now(timezone.utc))
            except Exception:   # not answering (busy with another reader), or not a monitor
                row = None
            if row is not None:
                break
            if attempt + 1 < READ_TRIES:
                time.sleep(READ_RETRY_WAIT)
        if row is None:
            lines.append(f"{kind}: found on your Wi-Fi, but it didn't answer a read as that monitor: check it is on, then run setup again")
            if item.serial in keep:
                held.add(kind)
            continue
        chosen[kind] = item
    out = [f"{kind}: found on your Wi-Fi, read directly" for kind in KINDS if kind in chosen]
    out += [also_found_line(kind, item) for kind, item in sorted(extra, key=lambda e: (KINDS.index(e[0]), e[1].serial))]
    return chosen, out + lines


def write_found(root, chosen, unit_country):
    """config.toml for the monitors found: [place] timezone and per kind its name and serial; [profiles] each false;
    [home] temp_unit from the monitor's own /config (f or F is F, else C) and [sources] smoke = false when its country
    isn't US. A config.toml that exists keeps every other line (a monitor already named keeps its name) and gets a
    backup first, only when it changes. Returns the path."""
    path = root / "config.toml"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old is None:
        lines = ["[place]", f"timezone = {json.dumps(config.local_timezone())}"]
        for kind in KINDS:
            if kind in chosen:
                lines += [f"{kind} = {json.dumps(phrases.PLACE_DEFAULTS[kind])}", f'{kind}_serial = "{chosen[kind].serial}"']
        # no profile yet: an absent [profiles] means every one (config.py), and "Who lives here" starts unset
        lines += ["", "[profiles]", *(f"{key} = false" for key in PROFILES)]
        unit = str(unit_country.get("temperatureUnit") or "")
        if unit:
            lines += ["", "[home]", f"temp_unit = {json.dumps('F' if unit.upper() == 'F' else 'C')}"]
        country = str(unit_country.get("country") or "")
        if country and country.upper() != "US":
            lines += ["", "[sources]", "smoke = false"]
        text = "\n".join(lines) + "\n"
    else:
        place = parsed_place(old)
        values = {}
        for kind in chosen:
            kv = {f"{kind}_serial": chosen[kind].serial}
            if not str(place.get(kind, "")).strip():
                kv[kind] = phrases.PLACE_DEFAULTS[kind]
            if str(place.get(f"{kind}_serial", "")).lower() != chosen[kind].serial:
                # the id was the account's choice, not proven to be this monitor's (a shared Wi-Fi has others): it goes,
                # and with a token the app matches this serial to the account at its next start (adopt_saved_token)
                kv[f"{kind}_id"] = None
            values[kind] = kv
        text = config.set_monitor_keys(old, values)
    if text != old:
        if old is None:
            path.write_text(text, encoding="utf-8")
        else:
            config.backup_config(path)
            config.replace_text(path, text)   # whole or not at all: an interrupted run never leaves half a home
    return path


def exact_place(place):
    """The (lat, lon) of a parsed [place] as config.toml keeps it, or None when it has none."""
    try:
        return round(float(place["lat"]), EXACT_DECIMALS), round(float(place["lon"]), EXACT_DECIMALS)
    except (KeyError, TypeError, ValueError):
        return None


def find_roads_step(path, previous):
    """The home's roads, found from the exact place in config.toml and written there (road_data.setup_roads), and the
    lines that say what was found. No question, and the exit code never depends on it: a failure is a line."""
    try:
        res = road_data.setup_roads(path, previous, get=requests.get)
    except Exception:  # the roads are an extra: they never stop setup
        return ["Roads: couldn't get the road data; Home Air on this computer tries again at its next start, at most once a day."]
    return res.lines


def parsed_place(text):
    """config.toml's [place], or a SetupError in one plain line when the file isn't valid TOML (never a traceback)."""
    try:
        return tomllib.loads(text).get("place", {})
    except tomllib.TOMLDecodeError as error:
        raise SetupError(f"config.toml can't be read ({error}). Fix that line, or move the file aside, then run setup again.") from None


def known_serials(root):
    """The serials already in config.toml, so a quiet monitor can be found by name; [] when there is none or it can't be read."""
    try:
        place = tomllib.loads((root / "config.toml").read_text(encoding="utf-8")).get("place", {})
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return []
    return [str(place[k]).lower() for k in ("indoor_serial", "outdoor_serial") if place.get(k)]


def monitor_settings(chosen):
    """The unit and country from the first chosen monitor's own /config that answers; {} when none does."""
    for kind in KINDS:
        if kind in chosen:
            try:
                return localnet.read_config(chosen[kind].address, chosen[kind].port)
            except Exception:   # the unit and the country are a convenience: the config's defaults apply
                continue
    return {}


def configured_kind(root, serial):
    """The kind config.toml reads this serial as, else None."""
    try:
        place = tomllib.loads((root / "config.toml").read_text(encoding="utf-8")).get("place", {})
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    return next((k for k in KINDS if str(place.get(f"{k}_serial") or "").lower() == serial), None)


def switch_monitor(serial, env, root):
    """--monitor: read this monitor instead of the one of its kind read now. It must be on the Wi-Fi now; its kind comes
    from its model. config.toml gets its serial (with a backup), loses that kind's cloud id (it was the other monitor's:
    with a token the app matches this serial at its next start), and the app restarts. With a token, a monitor the
    account doesn't have is refused: the next start would read the account's monitor instead (sources.match_account)."""
    serial = str(serial).strip().lower()
    kind = configured_kind(root, serial)
    if kind:
        print(f"The {kind} monitor read now is already {serial}: nothing to change.")
        return 0
    found = localnet.discover(env=env, serials=[*known_serials(root), serial] if config.SERIAL_RE.fullmatch(serial) else known_serials(root))
    item = next((f for f in found if f.serial == serial), None)
    if item is None:
        listed = ", ".join(f"{localnet.kind_of(f.model) or f.model} {f.serial} ({f.model})" for f in found)
        print(f"{serial} isn't on this Wi-Fi now. " + (f"Found: {listed}." if listed else NO_MONITOR[localnet.environment_blocker(env)] + "."))
        return 1
    chosen, lines = found_monitors([item])
    if not chosen:
        print("\n".join(lines))
        return 1
    kind = next(iter(chosen))
    token = env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root))
    if token:
        try:
            rows = account_rows(token)
        except SetupError:
            rows = []   # the account didn't answer: the app's next start matches the serial, as after setup
        serials = {str(r.get("serialno") or "").strip().lower() for r in rows if isinstance(r, dict)} - {""}
        if serials and serial not in serials:
            print(f"{serial} isn't in your AirGradient account, so Home Air would go back to the account's {kind} monitor. "
                  f"Add {serial} to the account in AirGradient's dashboard first.")
            return 1
    print(f"{kind}: {serial} ({item.model}), read directly from now on")
    print(f"Settings: {write_found(root, chosen, monitor_settings(chosen))}")
    return start_app(env, root)


def read_serials(root):
    """{kind: serial} for the monitors config.toml reads on the Wi-Fi; {} when there is none or it can't be read."""
    try:
        place = tomllib.loads((root / "config.toml").read_text(encoding="utf-8")).get("place", {})
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return {}
    return {k: str(place[f"{k}_serial"]).lower() for k in KINDS if place.get(f"{k}_serial")}


def list_monitors(env, root):
    """--monitors: the monitors found on the Wi-Fi now, one line each, saying which ones Home Air reads and the command
    that reads another one instead. Changes nothing: one browse of the Wi-Fi, no read, no write."""
    read = read_serials(root)
    found = localnet.discover(env=env, serials=list(read.values()))
    if not found:
        print(NO_MONITOR[localnet.environment_blocker(env)] + ".")
    for item in found:
        kind = localnet.kind_of(item.model)
        why = localnet.not_readable(item)
        if item.serial in read.values():
            state = "read by Home Air"
        elif why:
            state = f"not read: {why}"
        else:
            state = f"not read. To read it{' instead' if kind in read else ''}: {MONITOR_CMD} {item.serial}"
        print(f"{kind or 'monitor'} {item.serial} ({item.model}): {state}")
    for kind, serial in read.items():
        if serial not in {f.serial for f in found}:
            print(f"{kind} {serial}: read by Home Air, but it didn't answer on the Wi-Fi now")
    return 0


def find_home(env, root):
    """Setup's first step, asking nothing: find the monitors on the Wi-Fi, say what was found, write config.toml.
    Nothing found is not an error: the same file is written without a monitor and the lines say what to do next."""
    known = known_serials(root)
    found = localnet.discover(env=env, serials=known)
    chosen, lines = found_monitors(found, keep=known)
    unit_country = monitor_settings(chosen)
    if not chosen:
        blocker = localnet.environment_blocker(env)
        if not found:
            lines.append(NO_MONITOR[blocker])
        if blocker == "container":
            lines.append(f"Enter the token on the computer that ran setup, then run {HOME_SERVER_CMD} again.")
    print("\n".join(lines))
    if chosen or not (root / "config.toml").exists():
        print(f"Settings: {write_found(root, chosen, unit_country)}")
    return 0


def setup(args, env, root):
    prepare_env(root)
    profiles = [x for x in re.split(r"[,\s]+", (args.profiles or "none").lower()) if x and x != "none"]
    if any(p not in PROFILES for p in profiles):
        raise SetupError(f"Choose profiles from: {', '.join(PROFILES)}, or none. See docs/optional.md for adding one.", 2)
    token = env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root))
    if not token or (not args.address and args.lat is None and args.lon is None):   # the account's monitors only with a place
        return set_home(args, profiles, env, root)
    rows = account_rows(token)
    monitors = choose_monitors(rows, args)
    place = resolve_place(args)
    outdoor = outdoor_source(place) if "outdoor" not in monitors else None
    path = root / "config.toml"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        previous = exact_place(tomllib.loads(old).get("place", {})) if old is not None else None
    except tomllib.TOMLDecodeError:
        previous = None   # an old file that doesn't read is replaced below, as before
    if old is None or not _reads(old):
        text = config.set_monitor_keys(build_config(place, monitors, profiles, outdoor), kept_monitor_keys(old, monitors))
    else:   # a config.toml that reads: updated in place, so the owner's roads, limits, profiles and comments stay
        text = update_config(old, place, monitors, profiles, outdoor, args.profiles is not None)
        profiles = [k for k, v in (tomllib.loads(text).get("profiles") or {}).items() if v is True]
    cfg = load_config({"CONFIG_TOML": text}, root)
    if old != text:
        if old is not None:
            backup = config.backup_config(path)
            print(f"Saved the previous config at {backup}")
        path.write_text(text, encoding="utf-8")
    print(f"Home: {place.name}, {place.lat}, {place.lon}; timezone {place.timezone}")
    print("\n".join(find_roads_step(path, previous)))
    print(f"Who lives here: {', '.join(profiles) or 'none'}")
    print(f"Temperatures in °{cfg.temp_unit}" + (" (a US home)" if cfg.temp_unit == "F" else "") + ": change [home] temp_unit in config.toml to switch")
    if outdoor is not None:
        print(f"Outdoor air: {('estimate for your area (' + outdoor + ')') if isinstance(outdoor, str) else 'public monitor ' + str(outdoor)}")
    print(f"Settings: {path}")
    code = check_config.main(["--file", str(path)], env, root)
    if code:
        return code
    # The discovery call already read each monitor once; verify those same readings, without another request.
    problems = []
    for kind, row in monitors.items():
        age, words = age_words(row.get("timestamp"), datetime.now(timezone.utc))
        name = getattr(cfg.place, kind)
        print(f'{kind} "{name}", {words}' if name != phrases.PLACE_DEFAULTS.get(kind) else f"{kind}, {words}")
        if age is None or age > publicmap.STALE_READING:
            problems.append(f'The {kind} monitor has no recent reading. Check that it is plugged in and online.')
    for problem in problems:
        print(problem)
    print("Not live yet: fix the readings above and run setup again." if problems else "Live OK")
    return 1 if problems else 0


def set_home(args, profiles, env, root):
    """--address/--lat/--lon and --profiles without a token: the place and who lives here, written into the config.toml
    the Wi-Fi setup made (its monitors stay as they are), with a backup. With only --profiles, the profiles alone."""
    path = root / "config.toml"
    if not path.exists():
        raise SetupError(f"No config.toml yet: run {SETUP_CMD} first, then this command again.")
    old = path.read_text(encoding="utf-8")
    current = parsed_place(old)
    text = config.set_keys(old, "profiles", {key: key in profiles for key in PROFILES}) if args.profiles is not None else old
    place = None
    if args.address or args.lat is not None or args.lon is not None:
        place = resolve_place(args)
        values = {"name": place.name, "lat": place.lat, "lon": place.lon, "timezone": place.timezone}
        if place.country == "US" and place.zip:
            values["pollen_zip"] = place.zip
        if place.region:
            values["region"] = place.region
        text = config.set_keys(text, "place", values)
        if not (current.get("outdoor_serial") or current.get("outdoor_id")):   # no outdoor monitor: the outdoor source, once
            text = config.set_keys(text, "sources", {"outdoor_from": outdoor_source(place)})
        if place.country and place.country != "US":
            text = config.set_keys(text, "sources", {"smoke": False})
        if "temp_unit" not in (tomllib.loads(old).get("home") or {}):
            text = config.set_keys(text, "home", {"temp_unit": temp_unit(place)})
    load_config({"CONFIG_TOML": text}, root)   # a bad value stops here, before the file changes
    if text != old:
        backup = config.backup_config(path)
        config.replace_text(path, text)
        print(f"Saved the previous config at {backup}")
    if place is not None:
        print(f"Home: {place.name}; timezone {place.timezone}. The exact location stays in config.toml on this computer; "
              "what is sent for weather and pollen, and what the page shows, is rounded to about 1 km.")
        print("\n".join(find_roads_step(path, exact_place(current))))
    if args.profiles is not None:
        print(f"Who lives here: {', '.join(profiles) or 'none'}")
    print(f"Settings: {path}")
    code = check_config.main(["--file", str(path)], env, root)
    if not code:
        print(f"Restart Home Air to use them: {START_CMD}")
    return code


NO_WEB_WITHOUT_TOKEN = ("Home Air on Railway reads your monitors through AirGradient's Cloud API, so it needs your AirGradient API token. "
                        f"Run {CONNECT_CMD} to add it on this computer, then run this again.")


def railway(service, root):
    keys = saved_keys(root)
    if not env_key("AIRGRADIENT_TOKEN", keys):
        raise SetupError(NO_WEB_WITHOUT_TOKEN)
    # a web copy reads the cloud only: the monitors' addresses (a home-server copy's) never leave this computer
    text = config.set_monitor_keys((root / "config.toml").read_text(encoding="utf-8"), {k: {f"{k}_host": None} for k in KINDS})
    try:
        place = tomllib.loads(text).get("place", {})
    except tomllib.TOMLDecodeError:
        place = {}   # online_roads below refuses text that doesn't read: nothing is sent
    if "lat" in place and "lon" in place:   # the exact location stays on this computer: Home Air online gets it rounded
        text = config.set_keys(text, "place", {"lat": round(float(place["lat"]), PLACE_DECIMALS),
                                               "lon": round(float(place["lon"]), PLACE_DECIMALS)})
    try:
        text = config.online_roads(text)
    except config.ConfigError as e:
        raise SetupError(str(e)) from None
    # the roads' arcs in degrees stay on this computer: Home Air online gets 8-point sectors
    values = {"CONFIG_TOML": text}
    values.update((name, env_key(name, keys)) for name in KEY_NAMES if env_key(name, keys))
    for name, value in values.items():
        try:
            # --skip-deploys: each variable would otherwise start its own deploy, empty or failing before the code is up.
            subprocess.run(["railway", "variable", "set", name, "--stdin", "--service", service, "--skip-deploys"],
                           input=value, text=True, capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            raise SetupError(f"Railway did not accept {name}. Check the service name and CLI login, then try again.") from None
        print(f"Sent {name}")
    print(f"Variables set. Deploy with (ask the user first): railway up --service {service} --detach (first deploy), "
          f"or railway redeploy --service {service} --yes (a service already running)")
    return 0


def port_from(env):
    """The server's port: PORT when it is a number, else the default (server.py would not start on a bad one)."""
    try:
        port = int(env.get("PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


APP_RE = re.compile(r"window\.APP = (\{.*?\});</script>", re.S)


def age_words(ts, now):
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None, "no time on the last reading"
    if t.tzinfo is None:
        return None, "no time on the last reading"
    secs = (now - t).total_seconds()
    mins = int(secs // 60)
    words = ("just now" if mins < 1 else f"{mins} min ago" if mins < 60 else
             f"{mins // 60} h ago" if mins < 24 * 60 else f"{mins // (24 * 60)} days ago")
    return now - t, f"last reading {words}"


def live(url, env, root, get=None, now=None, wait=0, reach=None, platform=sys.platform):
    """The read-only check of a running page. `wait` seconds of retries when it doesn't answer yet (an app just started).
    `reach` (localnet.reach) and `platform` are for blocked_by_macos's diagnosis."""
    get = get or (lambda u: requests.get(u, timeout=LIVE_TIMEOUT))
    now = now or datetime.now(timezone.utc)
    base = url.rstrip("/")
    problems = []

    def problem(text):
        problems.append(text)
        print(f"Problem: {text}")

    end = time.monotonic() + wait
    while True:
        try:
            health = get(f"{base}/health").json()
            states = health.get("monitors") if isinstance(health, dict) else None
            if isinstance(states, dict) and "stale" in states.values() and time.monotonic() < end:
                time.sleep(WAIT_POLL_SECONDS)   # an app just started has not read its monitors yet: wait for the first reads
                continue
            break
        except Exception:   # not running, wrong port, not this app
            if time.monotonic() < end:
                time.sleep(WAIT_POLL_SECONDS)
                continue
            health = None
            break
    if health is None:
        print(f"The page doesn't answer at {base}. Start Home Air ({START_CMD}), or pass the page's address: "
              f"{CHECK_CMD} http://localhost:<port>")
        return 1
    if not isinstance(health, dict) or health.get("status") != "ok":
        print(f"{base}/health didn't say ok: is this Home Air?")
        return 1
    print(f"Page: answers at {base}")
    token = bool(health.get("airgradient_token_set"))
    print(f"Token: {'set' if token else 'missing'}")

    cfg = None
    try:
        cfg = load_config({k: v for k, v in env.items() if k != "DEMO"}, root)
    except ConfigError as e:
        print(f"Note: config.toml here doesn't load ({e}), so the page's settings weren't compared.")
    try:
        m = APP_RE.search(get(f"{base}/").text)
        app = json.loads(m.group(1)) if m else None
    except Exception:
        app = None
    if app is None:
        problem("the page has no settings (window.APP): is this Home Air?")
    elif "now" in app:
        problem("the page runs the demo (made-up data). Restart Home Air from the folder that has config.toml.")
    elif cfg is not None:
        settings_check(app, cfg, problem)

    blocked = blocked_by_macos(health, cfg, base, env, root, reach or localnet.reach, platform)
    where = monitors_where(health, cfg, problem, blocked)
    if token and (where is None or any(w != "local" for w in where.values())):
        current_check(get, base, cfg, now, problem)
    all_local = bool(where) and all(w == "local" for w in where.values())
    if all_local and not token:
        print("Note: optional: charts start from now (the AirGradient API token adds the last 7 days)")
    if problems:
        print(f"Not live yet: fix the problems above, then run {CHECK_CMD} again.")
        return 1
    if not token and not all_local:
        print("Not live yet: add AIRGRADIENT_TOKEN to .env and restart the app.")
        return 1
    print("Live OK")
    return 0


def running_health(env, root):
    """/health of the page running here (its port as --live finds it), or None when nothing answers: a GET, no more."""
    try:
        reply = requests.get(f"http://localhost:{started_port(env, root)}/health", timeout=STATUS_TIMEOUT)
        body = reply.json()
    except (requests.RequestException, ValueError):
        return None
    return body if isinstance(body, dict) else None


def monitors_where(health, cfg, problem, blocked=None):
    """One line per monitor from /health's `monitors` (kind -> local|cloud|stale), in the words the page uses
    (localnet.WHERE); None when the page doesn't say. A monitor that isn't answering is a problem. `blocked` (what
    blocked_by_macos names) turns the problem into its likely cause and fix; with only cloud readings, a note."""
    states = health.get("monitors")
    if not isinstance(states, dict) or not states:
        return None
    out = {}
    for kind in ("indoor", "outdoor"):
        if kind not in states:
            continue
        state = states[kind] if states[kind] in localnet.WHERE else "stale"
        name = getattr(cfg.place, kind, None) if cfg is not None else None
        label = f'{kind} "{publicmap.plain_text(name)}"' if name and name != phrases.PLACE_DEFAULTS.get(kind) else kind   # a default name (indoor "home") reads oddly
        print(f"{label}: {localnet.WHERE[state]}")
        if state == "stale" and not blocked:
            problem(f"the {kind} monitor is {localnet.WHERE['stale']}: is it plugged in and on this Wi-Fi?")
        out[kind] = state
    if blocked:
        line = (f"the monitors answer on this Wi-Fi, but Home Air doesn't read them: macOS's Local Network "
                f"permission is likely off for {blocked}. In {LOCAL_NETWORK}, turn it on for {blocked}, then run "
                f"{CHECK_CMD} again.")
        if "stale" in out.values():
            problem(line)
        else:
            print(f"Note: {line[0].upper()}{line[1:]}")
    return out


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")   # a page on this computer: the only one --live can compare with


def blocked_by_macos(health, cfg, base, env, root, reach, platform):
    """On a Mac, the program macOS's Local Network permission likely blocks ("uv" for the login item's copy, else the
    app that started it), in words, or None. Only when the page runs on this computer and a monitor with a serial isn't read
    on the Wi-Fi while this process finds it by name and reads it, or finds it and gets "No route to host" (the
    permission's symptom). Read-only: a name lookup and a GET per such monitor."""
    states = health.get("monitors") if isinstance(health, dict) else None
    if platform != "darwin" or cfg is None or cfg.place is None or not isinstance(states, dict):
        return None
    parsed = urllib.parse.urlsplit(base)
    if parsed.hostname not in LOCAL_HOSTS:
        return None
    serials = [getattr(cfg.place, f"{k}_serial", None) for k in KINDS if states.get(k) in ("stale", "cloud")]
    if not any(reach(s) in ("reads", "no_route") for s in serials if s):
        return None
    import autostart
    item = autostart.login_item(root, Path(env.get("HOME") or Path.home()))
    login_port = item[1] if item else None
    # macOS allows command-line tools run from Terminal or over SSH by itself (TN3179); another app that started it
    # (an agent's own app) is asked for like any app
    return '"uv"' if login_port == (parsed.port or 80) else "the app Home Air was started from"


def settings_check(app, cfg, problem):
    place = app.get("place") or {}
    p = cfg.place
    wanted = {"name": p.name, "indoor monitor": p.indoor, "outdoor monitor": p.outdoor}
    shown = {"name": place.get("name"), "indoor monitor": place.get("indoor"), "outdoor monitor": place.get("outdoor")}
    of = cfg.outdoor_from
    if of is not None:
        wanted["outdoor source"] = of
        o = app.get("outdoor_from") or {}
        shown["outdoor source"] = o.get("id") if o.get("kind") == "monitor" else o.get("kind")
    wanted["profiles"] = sorted(k for k, v in cfg.profiles.items() if v)
    shown["profiles"] = sorted(k for k, v in (app.get("profiles") or {}).items() if v)
    off = [k for k in wanted if wanted[k] != shown[k]]
    for k in off:
        problem(f"the page's {k} is {json.dumps(shown[k])}, config.toml's is {json.dumps(wanted[k])}. Restart Home Air "
                f"({START_CMD}): it reads config.toml when it starts.")
    if not off:
        print("Settings: the name, monitors, profiles" + (" and outdoor source" if of is not None else "")
              + " match config.toml")


def current_check(get, base, cfg, now, problem):
    try:
        r = get(f"{base}/api/current")
        data = r.json()
    except Exception as e:
        problem(f"/api/current didn't answer ({type(e).__name__}).")
        return
    if getattr(r, "status_code", 200) >= 400:
        err = data.get("error") if isinstance(data, dict) else None
        problem(f"AirGradient answered with an error: {publicmap.plain_text(err or r.status_code)}. Check the token "
                "in .env, then restart the server.")
        return
    if isinstance(data, dict) and data.get("state") == "no_token":
        problem(f"the page has no token yet. Add it at the address {CONNECT_CMD} prints, or fill AIRGRADIENT_TOKEN in .env and restart.")
        return
    rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    expect = ([k for k, n in (("indoor", cfg.place.indoor), ("outdoor", cfg.place.outdoor)) if n is not None]
              if cfg else ["indoor", "outdoor"])
    for kind in expect:
        wanted_id = getattr(cfg.place, kind + "_id") if cfg else None
        row = next((x for x in rows if x.get("locationType") == kind and "source" not in x
                    and (wanted_id is None or x.get("locationId") == wanted_id)), None)
        if row is None:
            problem(f"no {kind} monitor: the token's account has no monitor set as {kind}. Set one as {kind} in "
                    "AirGradient's dashboard, or check it's the right account's token.")
            continue
        age, words = age_words(row.get("timestamp"), now)
        name = publicmap.plain_text(row.get("locationName") or "") or f"Monitor {row.get('locationId')}"
        model = f" ({publicmap.plain_text(row['model'])})" if row.get("model") else ""
        print(f'{kind} "{name}"{model}, {words}')
        if age is None or age > publicmap.STALE_READING:
            problem(f'the {kind} monitor "{name}" has no recent reading: is it plugged in and online?')
    if cfg is not None and cfg.place.outdoor is None:
        src = next((x for x in rows if isinstance(x.get("source"), dict)), None)
        if src is None:
            problem("no outdoor source on the page, though the home has no outdoor monitor.")
            return
        s = src["source"]
        if s.get("kind") == "monitor":
            _, words = age_words(src.get("timestamp"), now)
            km = f", {s['distance_km']:.1f} km" if isinstance(s.get("distance_km"), (int, float)) else ""
            print(f'outdoor source: public monitor "{publicmap.plain_text(s.get("name") or s.get("id"))}"{km}, {words}')
        elif s.get("kind") == "area":
            print("outdoor source: the estimate for your area"
                  + (f", from {s['area_count']} public monitors" if s.get("area_count") else ""))
        else:
            print("outdoor source: Open-Meteo's forecast for your area")
        if s.get("fallback"):
            print("Note: the chosen public monitor isn't reporting now (or AirGradient's map didn't answer): the page "
                  "shows the estimate for your area until it's back.")


def status(env, root):
    """What's done in each of setup's three states (SETUP.md): a first look, your home, ready to deploy; then the one
    next step. Read-only: writes nothing, asks nothing; its one request is a GET of the running page's /health. Keys
    are reported as added or not, never their values."""
    import autostart
    env = {k: v for k, v in env.items() if k != "DEMO"}
    home = Path(env.get("HOME") or Path.home())
    port = started_port(env, root)
    nexts = []
    cfg, problem = None, None
    try:
        cfg = load_config(env, root)
    except ConfigError as e:
        problem = str(e)
    token = check_config.keys_present(env, root)["AIRGRADIENT_TOKEN"]
    health = running_health(env, root)

    print("A first look")
    if cfg is None:
        print(f"  Settings: {problem or 'no config.toml yet'}")
        nexts.append(SETUP_CMD)
    if health is None:
        print(f"  Home Air on this computer: not running")
        nexts.append(START_CMD)
    else:
        print(f"  Home Air on this computer: running at http://localhost:{port}")
        where = health.get("monitors") if isinstance(health.get("monitors"), dict) else {}
        if cfg is not None and where:
            print("  Readings: " + "; ".join(f"{k}: {localnet.WHERE.get(v, localnet.WHERE['stale'])}" for k, v in where.items()))
    login = autostart.plist_path(home).exists() or autostart.unit_path(home).exists()
    print(f"  Starts at login: {'yes' if login else f'no ({LOGIN_ON_CMD})'}")

    print("Your home")
    place = cfg.place if cfg is not None else None
    has_place = place is not None and place.name is not None
    print(f"  Address: {place.name if has_place else 'not set (to set it: say "set up my home in Home Air" to a coding agent in this folder)'}")
    on = [k for k, v in cfg.profiles.items() if v] if cfg is not None else []
    print(f"  Who lives here: {', '.join(on) or 'none set'}")
    print(f"  AirGradient API token: {'added' if token else f'not added (to add your past readings to the charts: {CONNECT_CMD})'}")
    linked = bool(agent_record(root)["links"])
    url = (root / URL_FILE).read_text(encoding="utf-8").strip() if (root / URL_FILE).exists() else ""
    print(f"  Skill for coding agents: {('added' + (f', reads {url}' if url else '')) if linked else f'not added (to ask about the air from any folder: {AGENT_ON_CMD})'}")
    if cfg is not None and not has_place:
        nexts.append('open a coding agent in this folder and say "set up my home in Home Air"')

    print("On a home server or online")
    missing = [w for w, ok in (("the AirGradient API token", token), ("the address", has_place)) if not ok]
    print(f"  Home Air online needs: {' and '.join(missing)}" if missing else
          "  Home Air online: ready (DEPLOYMENT.md); a home server: ./homeair home-server")
    print(f"Next: {nexts[0]}" if nexts else f"Next: nothing needed. To check that Home Air answers: {CHECK_CMD}")
    return 0


def page_answers(url, env, root, quiet=False):
    """After the system starts the app: wait for /health, then the --live check (quiet: its lines not printed, when
    the one-command setup prints the same check at its end)."""
    end = time.monotonic() + STARTUP_WAIT
    while time.monotonic() < end:
        try:
            if requests.get(f"{url}/health", timeout=WAIT_POLL_SECONDS).ok:
                if not known_serials(root) and not has_saved_token(env, root):
                    return 0   # nothing to read yet (no monitor found, no token): the page answering is the check
                if quiet:
                    with contextlib.redirect_stdout(io.StringIO()):
                        return live(url, env, root)
                return live(url, env, root)
        except requests.RequestException:
            pass
        time.sleep(WAIT_POLL_SECONDS)
    return 1


def started_port(env, root):
    """The port --start chose, else PORT or 5555: so a bare --live checks the copy just started."""
    try:
        port = int((root / PORT_FILE).read_text().strip())
        return port if 0 < port < 65536 else port_from(env)
    except (OSError, ValueError):
        return port_from(env)


def page_up(port):
    """This computer's app answers /health on that port."""
    try:
        return requests.get(f"http://127.0.0.1:{port}/health", timeout=WAIT_POLL_SECONDS).ok
    except requests.RequestException:
        return False


def restart_login_copy(kind, port, env, root):
    """The login item's copy runs: the system restarts it with the settings just written, rather than a second copy
    on another port (two copies would read the monitors twice and write one history file). Waits until the new
    process answers: its pid in app.pid, then /health."""
    import autostart
    pid_file = root / autostart.PID_FILE
    before = pid_file.read_text().strip() if pid_file.exists() else None
    if autostart.restart(kind, lambda cmd: subprocess.run(cmd, capture_output=True).returncode) != 0:
        print(f"Home Air started at login didn't restart. Its log: app.log. Turn start at login on again: {LOGIN_ON_CMD}")
        return 1
    end = time.monotonic() + STARTUP_WAIT
    while time.monotonic() < end:
        now = pid_file.read_text().strip() if pid_file.exists() else None
        if now and now != before and page_up(port):
            break
        time.sleep(1)
    else:
        print(f"Home Air started at login isn't answering yet at http://localhost:{port}. See app.log, then run {START_CMD} again.")
        return 1
    (root / PORT_FILE).write_text(f"{port}\n")
    print(f"Restarted: http://localhost:{port}, Home Air started at login (log: app.log)")
    if not has_saved_token(env, root):
        print_connect_link(port, root)
    return 0


def start_app(env, root):
    """Start the app in the background and wait until it answers, so the next step can't run before it is up. When
    the login item's copy for this folder answers, the system restarts that one instead. Otherwise a copy setup
    started before is stopped first, and a taken port moves to a free one."""
    import autostart
    item = autostart.login_item(root, Path(env.get("HOME") or Path.home()))
    if item is not None and autostart.port_answers(item[1]):
        return restart_login_copy(*item, env, root)
    autostart.stop_setup_copy(root)
    port = port_from(env)
    tries = iter(range(FALLBACK_PORT, FALLBACK_PORT + FALLBACK_TRIES))
    while autostart.port_answers(port):
        port = next(tries, None)
        if port is None:
            print(f"No free port from {FALLBACK_PORT} to {FALLBACK_PORT + FALLBACK_TRIES - 1}. Stop what's using them, or set PORT.")
            return 1
    log = root / "app.log"
    with open(log, "wb") as out:
        child = subprocess.Popen([str(root / RUN), "server.py"], cwd=root, env={**env, "PORT": str(port)}, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    end = time.monotonic() + STARTUP_WAIT
    while time.monotonic() < end:
        if child.poll() is not None:
            tail = log.read_text(errors="replace").strip().splitlines()[-5:]
            print("The app stopped right after starting. Last lines of app.log:\n" + "\n".join(tail))
            return 1
        try:
            if requests.get(f"http://127.0.0.1:{port}/health", timeout=WAIT_POLL_SECONDS).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        print(f"The app isn't answering yet at http://localhost:{port}. See app.log, then run {START_CMD} again.")
        return 1
    (root / PORT_FILE).write_text(f"{port}\n")   # so `--live` with no address checks this copy, not another app on 5555
    print(f"Started: http://localhost:{port} (log: app.log)")
    if not has_saved_token(env, root):
        print_connect_link(port, root)
    return 0


def stop_app(env, root):
    """--stop: stop the copy --start started, by the pid it recorded in app.pid (autostart.stop_setup_copy checks that
    pid is this app's server.py). The copy that starts at login is the system's to restart, so it is left running and
    the message names the command that turns it off."""
    import autostart
    item = autostart.login_item(root, Path(env.get("HOME") or Path.home()))
    if item is not None and autostart.port_answers(item[1]):
        print(f"Home Air at http://localhost:{item[1]} is Home Air started at login, and the system starts it again "
              f"when it stops. To stop it, turn start at login off: {LOGIN_OFF_CMD}")
        return 1
    if autostart.stop_setup_copy(root):
        print(f"Stopped Home Air (it was at http://localhost:{started_port(env, root)}). To start it again: {START_CMD}")
    else:
        print(f"Home Air isn't running from this folder: nothing to stop. To start it: {START_CMD}")
    return 0


def has_saved_token(env, root):
    return bool(env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root)))


def token_link(port):
    """The token form's address on this computer's page."""
    return f"http://localhost:{port}/connect"


def print_connect_link(port, root):
    """Printed for the agent to open: the page's token form."""
    print(f"Add your AirGradient API token at {token_link(port)}")


def connect_link(env, root):
    """--connect: the token form's address, for the Home Air --start started (or PORT, or 5555)."""
    port = started_port(env, root)
    if has_saved_token(env, root):
        print(f"A token is already saved: nothing to connect. The page: http://localhost:{port}")
        return 0
    print_connect_link(port, root)
    return 0


def start_at_login(state, env, root, quiet=False):
    import autostart
    run = lambda cmd: subprocess.run(cmd, capture_output=True).returncode
    home = Path(env.get("HOME") or Path.home())
    if state == "off":
        return autostart.turn_off(root, home, run, shutil.which, sys.platform)
    if not str(env.get("PORT") or "").strip():   # the port setup or --start chose (5555 may be another program's)
        env = {**env, "PORT": str(started_port(env, root))}
    url = f"http://localhost:{port_from(env)}"
    code = autostart.turn_on(root, home, env, run, shutil.which, sys.platform, lambda: page_answers(url, env, root, quiet=quiet))
    if code == 0:   # the port the login item serves: --live, --connect and --status ask app.port's
        (root / PORT_FILE).write_text(f"{port_from(env)}\n")
        if sys.platform == "darwin" and known_serials(root) and not wait_wifi_reads(url, kinds_read_locally(root)):
            return 1   # it said what to turn on; one_run goes on while the page answers
    return code


LOGIN_READ_WAIT = 60     # s: how long setup waits for the login item's first reading on the Wi-Fi; macOS holds it until "uv" is allowed
LOGIN_READ_QUIET = 4     # s: before saying to press Allow; an allowed copy read both monitors 0.9 s after starting (22 Nov)
LOCAL_NETWORK = "System Settings › Privacy & Security › Local Network"   # where macOS keeps the Local Network permission


def kinds_read_locally(root):
    """The kinds config.toml gives a serial: the monitors Home Air reads on the Wi-Fi."""
    try:
        place = tomllib.loads((root / "config.toml").read_text(encoding="utf-8")).get("place", {})
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return []
    return [k for k in KINDS if place.get(f"{k}_serial")]


def wait_wifi_reads(url, kinds, get=None, clock=time.monotonic, sleep=time.sleep):
    """After the login item starts on a Mac: wait up to LOGIN_READ_WAIT for its first reading on the Wi-Fi of each kind
    (macOS holds every local connection until the user answers its "uv" prompt), say to press Allow while it waits,
    then say what came of it. Read-only (GETs of /health); True when every kind reads on the Wi-Fi."""
    get = get or (lambda u: requests.get(u, timeout=STATUS_TIMEOUT))
    start, told = clock(), False
    states = {}
    while True:
        try:
            body = get(f"{url.rstrip('/')}/health").json()
            states = body.get("monitors") if isinstance(body, dict) else None
            states = states if isinstance(states, dict) else {}
        except Exception:   # restarting: ask again
            states = {}
        if kinds and all(states.get(k) == "local" for k in kinds):
            print(f"{', '.join(kinds).capitalize()}: read on your Wi-Fi by Home Air started at login.")
            return True
        waited = clock() - start
        if waited >= LOGIN_READ_WAIT:
            print(f"No reading on your Wi-Fi after {LOGIN_READ_WAIT} s. If macOS asked about \"uv\", turn on \"uv\" in "
                  f"{LOCAL_NETWORK}, then run {CHECK_CMD}.")
            return False
        if not told and waited >= LOGIN_READ_QUIET:
            print(f'Waiting for the first reading on your Wi-Fi (up to {LOGIN_READ_WAIT} s): press Allow on macOS\'s '
                  'prompt "Allow "uv" to find devices on local networks?".', flush=True)
            told = True
        sleep(WAIT_POLL_SECONDS)


def running_agent(env):
    """The coding agent running setup, from the variables it sets for its commands; None when it can't be told. It
    picks the folders to link, and counts as found (agents_found) for the message."""
    if env.get("CLAUDECODE"):
        return "Claude Code"
    if any(env.get(k) for k in CODEX_VARS):
        return "Codex"
    if env.get("GEMINI_CLI"):
        return "Gemini CLI"
    if env.get("CURSOR_AGENT"):
        return "Cursor"
    return None


def agent_targets(env, home):
    """Where --agent on links the skill: always the shared folder; Claude Code's own folder too when Claude Code runs
    setup or this computer has Claude Code (~/.claude), whichever agent runs it (Claude Code doesn't read the shared one)."""
    agent = running_agent(env)
    targets = [home / SHARED_SKILLS / SKILL]
    if agent == "Claude Code" or (home / ".claude").is_dir():
        targets.append(home / CLAUDE_SKILLS / SKILL)
    return agent, targets


def agents_found(env, home):
    """The coding agents on this computer, in AGENT_SIGNS' order: each one's folder in the home folder, or one of its
    commands on env's PATH. Only names them; where the skill is linked stays agent_targets' choice."""
    path = env.get("PATH", "")
    return [name for name, folder, commands in AGENT_SIGNS
            if (home / folder).is_dir() or any(shutil.which(c, path=path) for c in commands)]


def words_and(names):
    """"A", "A and B", "A, B and C"; "" for none."""
    names = list(names)
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


def links_to(path, source):
    """Whether `path` is a symlink to this folder's skill (also when this folder has moved since)."""
    if not path.is_symlink():
        return False
    target = os.readlink(path)
    return target == str(source) or path.resolve() == source.resolve()


def agent_record(root):
    try:
        data = json.loads((root / AGENT_FILE).read_text(encoding="utf-8"))
        return {"skill": str(data["skill"]), "links": [str(p) for p in data["links"]]}
    except (OSError, ValueError, KeyError, TypeError):
        return {"skill": "", "links": []}


def agent_skill(state, env, root):
    """--agent on|off|status: link this folder's skill into the agent's skills folders, as symlinks so a
    git pull updates it. Never replaces a skill that's already there; off removes only the links on made."""
    home = Path(env.get("HOME") or Path.home())
    source = root / "skills" / SKILL
    short = lambda p: "~/" + str(p.relative_to(home)) if p.is_relative_to(home) else str(p)
    record = agent_record(root)
    if state == "status":
        paths = list(dict.fromkeys([home / SHARED_SKILLS / SKILL, home / CLAUDE_SKILLS / SKILL] + [Path(p) for p in record["links"]]))
        found = agents_found(env, home)
        print(f"Coding agents on this computer: {words_and(found) if found else 'none found'}")
        print(f"Home Air skill ({SKILL}):")
        for p in paths:
            if links_to(p, source):
                words = "linked by setup" if str(p) in record["links"] else f"linked to this folder, not by setup ({AGENT_OFF_CMD} leaves it)"
            elif p.is_symlink() or p.exists():
                words = "a different skill with this name (left as it is)"
            else:
                words = "not linked"
            print(f"  {short(p)}: {words}")
        return 0
    if state == "off":
        if not record["links"]:
            print("Setup hasn't linked the Home Air skill anywhere: nothing to remove.")
            return 0
        for p in map(Path, record["links"]):
            if p.is_symlink() and os.readlink(p) == record["skill"]:
                p.unlink()
                print(f"Removed {short(p)}")
            elif p.is_symlink() or p.exists():
                print(f"Left {short(p)}: it's no longer the link made during setup")
        (root / AGENT_FILE).unlink(missing_ok=True)
        print("The Home Air skill is removed from your coding agents.")
        return 0
    if not (source / "SKILL.md").is_file():
        raise SetupError(f"This Home Air folder has no skills/{SKILL}. Update the folder with git pull, then run {AGENT_ON_CMD} again.")
    agent, targets = agent_targets(env, home)
    found = agents_found(env, home) + ([agent] if agent else [])   # before linking: a link can create ~/.claude
    ours_before = set(record["links"])   # links this setup made earlier, perhaps from a folder since moved
    if record["skill"] and record["skill"] != str(source):
        record["links"] = []   # a record from another folder: its links aren't this folder's to remove
    linked, foreign = [], []
    for p in targets:
        if links_to(p, source):
            linked.append(p)
        elif p.is_symlink() and not p.exists() and str(p) in ours_before:
            p.unlink()   # our own link, broken because the Home Air folder moved: link it again below
            p.symlink_to(source, target_is_directory=True)
            record["links"].append(str(p))
        elif p.is_symlink() or p.exists():
            foreign.append(p)
        else:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.symlink_to(source, target_is_directory=True)
            except OSError as e:
                print(f"Couldn't link {short(p)} ({e.strerror or e}). Check that folder's permissions, then run this again.")
                continue
            record["links"].append(str(p))
            linked.append(p)
            # recorded as soon as it's made, so --agent off removes it even if a later link fails
            (root / AGENT_FILE).write_text(json.dumps({"skill": str(source), "links": sorted(set(record["links"]))}, indent=2) + "\n")
    for p in foreign:
        print(f"Left {short(p)}: a different skill named {SKILL} is already there, and it isn't replaced.")
    if not linked:
        print("The skill wasn't added: see above." + (" Remove or rename that skill, then run this again." if foreign else ""))
        return 1
    folders = [p.parent.relative_to(home).as_posix() for p in linked]   # every target is under home (agent_targets)
    where = " and ".join(f"{short(p)} (read by {words_and(FOR_AGENTS.get(f, ('an agent',)))})" for p, f in zip(linked, folders))
    readers = {name for f in folders for name in FOR_AGENTS.get(f, ())}
    named = [name for name, _, _ in AGENT_SIGNS if name in found and name in readers]
    print(f"Added the Home Air skill to {words_and(named) or 'your coding agents'} on this computer: "
          f"linked this folder's skills/{SKILL} into {where}.")
    print("With the skill, an agent answers from your monitors, and changes your settings only when you ask and say yes.")
    print("Ask an agent about the air at home. If the agent's current session doesn't list the skill yet, start a new "
          "session. The first time, the agent asks before it runs the skill's script.")
    print(f"To remove the skill: {AGENT_OFF_CMD}")
    return 0


def skill_url(url, root):
    """skill add --url: the address the skill reads Home Air at, saved in this folder (URL_FILE); "local" goes back to
    this computer's. Checked once with a GET of /health, so a typo doesn't leave the skill reading nothing."""
    path = root / URL_FILE
    if url == "local":
        path.unlink(missing_ok=True)
        print("The Home Air skill reads Home Air on this computer again.")
        return 0
    url = url.strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
        raise SetupError(f'"{url}" isn\'t a full address. Use one like http://<server>.local:8000 or https://<name>.up.railway.app, then run the command again.', 2)
    try:
        ok = requests.get(f"{url}/health", timeout=LOOKUP_TIMEOUT).ok
    except requests.RequestException:
        ok = False
    if not ok:
        raise SetupError(f"Home Air didn't answer at {url}. Check the address, then run the command again.")
    path.write_text(url + "\n", encoding="utf-8")
    print(f"The Home Air skill now reads Home Air at {url}. To read Home Air on this computer again: {AGENT_ON_CMD} --url local")
    return 0


def open_page(url, env):
    """Open the page in this computer's browser; a message instead of an error when there is none. Skipped where
    there is no browser to open (a container, a hosted copy, WSL), which setup already said."""
    if localnet.environment_blocker(env) is not None:
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        shown = subprocess.run([opener, url], capture_output=True, timeout=OPEN_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        shown = False
    masked = re.sub(r"([?&]code=)[^&]+", r"\1…", url)   # opened: no need to print the private code again
    print(f"Opened {masked} in your browser." if shown else f"Open {url} in your browser.")


def one_run(env, root):
    """Bare setup: find the home, start it, keep it running, connect the agent, open the page, check it. It asks nothing.
    Nothing found is not a failure: the page opens on the token card and the run ends without the live check."""
    prepare_env(root)
    code = find_home(env, root)
    if code:
        return code
    code = start_app(env, root)
    if code:
        return code
    if start_at_login("on", env, root, quiet=True) != 0 and not page_up(started_port(env, root)):
        code = start_app(env, root)   # turning it on stopped the copy started above: bring it back for this session
        if code:
            return code
    print(f"To ask a coding agent about the air at home from any folder: {AGENT_ON_CMD}")
    port = started_port(env, root)
    url = f"http://localhost:{port}"
    # The home when there is something to read; the token form when nothing answered on the Wi-Fi and no token is saved
    open_page(url if has_saved_token(env, root) or known_serials(root) else token_link(port), env)
    if not has_saved_token(env, root) and not known_serials(root):
        # Written to the person, so an agent can pass it on as it is (SETUP.md) and someone running setup reads it the same way
        print(f"Nothing is read yet: no AirGradient monitor answered on your Wi-Fi, so Home Air needs your AirGradient "
              f"API token. The page that just opened at {url} shows how to add the token (about 1 min). "
              f"{CONNECT_CMD} --wait says when it's saved.")
        return 0
    if has_saved_token(env, root):
        try:
            print(connected_line(account_rows(env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root)))))
        except SetupError as error:
            print(error)
    return live(url, env, root, wait=LIVE_RETRY_SECONDS)


def parser():
    ap = argparse.ArgumentParser(description="Set up Home Air for a home. With no options: find the monitors on the Wi-Fi, "
                                             "start Home Air, add the skill, open the page and check it. Usually run as "
                                             "./homeair setup (./homeair help lists the short commands).")
    ap.add_argument("--address", help="an address, neighborhood or nearby landmark with its city")
    ap.add_argument("--profiles", help="toddler,sinus,runner, or none")
    ap.add_argument("--indoor-id", type=int, help="which indoor monitor, only when ambiguous")
    ap.add_argument("--outdoor-id", type=int, help="which outdoor monitor, only when ambiguous")
    ap.add_argument("--monitor", metavar="SERIAL",
                    help="read this monitor found on the Wi-Fi instead of the one of its kind read now")
    ap.add_argument("--monitors", action="store_true",
                    help="list the monitors found on the Wi-Fi and which ones Home Air reads; changes nothing")
    ap.add_argument("--name", help="a correction to the area's name")
    ap.add_argument("--lat", type=float, help="latitude, together with --lon")
    ap.add_argument("--lon", type=float, help="longitude, together with --lat")
    ap.add_argument("--zip", help="a correction to a US ZIP code")
    ap.add_argument("--region", help="a correction to the city or region")
    ap.add_argument("--timezone", help="a correction using an IANA timezone name")
    ap.add_argument("--live", nargs="?", const="", metavar="URL", help="check the running page with GET requests")
    ap.add_argument("--status", action="store_true", help="list what's done and the next command; changes nothing")
    ap.add_argument("--start", action="store_true", help="start the app in the background and wait until it answers")
    ap.add_argument("--stop", action="store_true", help="stop the Home Air that --start started")
    ap.add_argument("--connect", action="store_true", help="print the token form's address (no token saved yet)")
    ap.add_argument("--start-at-login", choices=("on", "off"),
                    help="start the app by itself at login (a Mac or Linux with systemd), or stop doing so")
    ap.add_argument("--home-server", action="store_true",
                    help="prepare a folder for a home server with Docker (Compose, settings, token) and say the two steps")
    ap.add_argument("--agent", choices=("on", "off", "status"),
                    help="add the Home Air skill to the coding agents on this computer (as links), remove it, or "
                         "show the links")
    ap.add_argument("--url", help="with --agent on: the address of Home Air the skill reads (Home Air online or on a home "
                                   "server), or local for this computer's")
    ap.add_argument("--wait-token", action="store_true",
                    help=f"wait up to {WAIT_TOKEN_SECONDS} s for the token page to save the token, then say what the account has")
    return ap


def main(argv, env, root=ROOT):
    if argv[:1] == ["railway"]:
        ap = argparse.ArgumentParser(description="Send saved config and keys to a Railway service through stdin.")
        ap.add_argument("--service", required=True)
        args = ap.parse_args(argv[1:])
    else:
        args = parser().parse_args(argv)
    try:
        if argv[:1] == ["railway"]:
            return railway(args.service, root)
        if args.status:
            return status(env, root)
        if args.wait_token:
            return wait_token(env, root)
        if args.monitor:
            return switch_monitor(args.monitor, env, root)
        if args.monitors:
            return list_monitors(env, root)
        if args.home_server:
            import homeserver
            return homeserver.prepare(root)
        if args.start:
            return start_app(env, root)
        if args.stop:
            return stop_app(env, root)
        if args.connect:
            return connect_link(env, root)
        if args.start_at_login:
            return start_at_login(args.start_at_login, env, root)
        if args.agent:
            code = agent_skill(args.agent, env, root)
            if args.agent == "off":
                (root / URL_FILE).unlink(missing_ok=True)
            return skill_url(args.url, root) if args.url and args.agent == "on" and not code else code
        if args.live is not None:
            return live(args.live or f"http://localhost:{started_port(env, root)}", env, root, wait=LIVE_RETRY_SECONDS)
        if not args.address and args.lat is None and args.lon is None and args.profiles is None:
            return one_run(env, root)
        return setup(args, env, root)
    except (SetupError, ConfigError, OSError, UnicodeError) as error:
        if isinstance(error, (SetupError, ConfigError)):
            print(str(error))
        else:   # name the file, so the person (or the agent's sandbox rules) can be fixed
            where = getattr(error, "filename", None)
            print(f"Couldn't read or write {where}: {getattr(error, 'strerror', None) or type(error).__name__}. Check its permissions."
                  if where else "Couldn't read or write a setup file. Check its permissions.")
        return error.code if isinstance(error, SetupError) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
