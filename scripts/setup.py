"""Prepare private files, discover monitors, write household choices and verify them.

Run with no answers to prepare .env; with --address and --profiles to finish setup.
The railway command transfers saved settings through stdin. --live checks a running page.
--status lists what's done and the next command; it changes nothing.
--agent on links the Home Air skill into the agent's skills folders; off removes only those links.
"""
import argparse
import json
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_config
import config
import localnet
import phrases
import publicmap
import sources  # installs the request-log filter that hides query credentials
import token_setup
from config import (ENV_FILE_MODE, PLACE_DECIMALS, PROFILES, ConfigError, defines,
                    env_key, load_config, parse_dotenv, public_view, write_env_key)

from upstream import USER_AGENT  # Nominatim requires an identifying User-Agent: the app's, HomeAir/<version>.
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"  # timezone=auto resolves a point's timezone.
LOOKUP_RESULTS = 3  # matches: enough to catch a name shared by different states.
AMBIGUOUS_SHOWN = 3  # matches shown when a place needs its city or state.
LOOKUP_TIMEOUT = 15  # seconds per place or timezone request.
LIVE_RETRY_SECONDS = 10  # --live keeps trying this long when the page doesn't answer yet: an app started a moment ago (a Codex run checked 5 times too early).
LIVE_TIMEOUT = 30  # seconds: a running page may be waiting on its outside sources.
DEFAULT_PORT = 5555  # server.py's local port without PORT.
AREA_CATEGORIES = ("place", "boundary")  # Nominatim's area names, rather than buildings or streets.
DISTRICT_KEYS = ("suburb", "city_district", "borough", "quarter")  # district before city for a street lookup.
CITY_KEYS = ("city", "town", "village", "hamlet")
NORTHEAST = {"CT": "Connecticut", "ME": "Maine", "MA": "Massachusetts", "NH": "New Hampshire", "RI": "Rhode Island",
             "VT": "Vermont", "NY": "New York", "NJ": "New Jersey", "PA": "Pennsylvania"}  # normalize known state codes.
NOT_FOUND = "Couldn't find it. Try a nearby landmark with its city, or pass --lat and --lon from a map app."
KEY_NAMES = ("AIRGRADIENT_TOKEN", "GOOGLE_POLLEN_API_KEY")  # the only keys transferred to Railway.
STATUS_TIMEOUT = 15  # seconds for --status's one readings request: AirGradient took 7 s to connect on 2026-11-13, and 5 s read that as a refused token.
RUN = "scripts/run.sh"  # how SETUP.md runs every command: uv, else installing uv, else Python 3.11+ (the uv ladder).
SETUP_CMD = f"{RUN} scripts/setup.py"
SERVER_CMD = f"{RUN} server.py"
START_CMD = f"{RUN} scripts/setup.py --start"  # starts it in the background, so it outlives the agent's session, and waits for the address; the log is in app.log
HOME_CMD = f'{SETUP_CMD} --address "<neighborhood or address>" --profiles <toddler,sinus,runner or none>'
STATUS_CMD = f"{SETUP_CMD} --status"
WAIT_TOKEN_CMD = f"{SETUP_CMD} --wait-token"
CONNECT_CMD = f"{SETUP_CMD} --connect"  # prints the private link to the token box again
WAIT_TOKEN_SECONDS = 90  # one wait: under the shortest default command limit we know (Claude Code stops a command after 120 s).
PORT_FILE = "app.port"  # the port --start chose, in the repo folder (ignored by git)
FALLBACK_PORT = 5600  # the first port --start tries when the usual one is taken.
FALLBACK_TRIES = 20  # ports tried from FALLBACK_PORT.
STARTUP_WAIT = 30  # seconds for the page to answer after the system starts it at login (uv may install packages first).
OPEN_TIMEOUT = 10  # seconds the browser opener gets: it returns at once, or something is wrong.
WAIT_POLL_SECONDS = 2  # how often --wait-token looks for the saved token: the user sees the agent move on within 2 s.
SKILL = "home-air-quality"  # the reading skill: its folder under skills/ here, and its name in each agent's skills folder
AGENT_FILE = "agent-links.json"  # what --agent on linked, in the repo folder (ignored by git), so --agent off removes only those
SHARED_SKILLS = ".agents/skills"  # under the home folder: the shared folder Codex, Cursor and Gemini CLI read
CLAUDE_SKILLS = ".claude/skills"  # under the home folder: Claude Code's own skills folder (it doesn't read the shared one)
AGENT_OFF_CMD = f"{SETUP_CMD} --agent off"
CODEX_VARS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID")  # set by Codex for its commands; not CODEX_HOME, a user setting
FOR_AGENTS = {SHARED_SKILLS: "Codex, Cursor and Gemini CLI", CLAUDE_SKILLS: "Claude Code"}  # who reads each skills folder


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
    return Place(lat=round(float(r["lat"]), PLACE_DECIMALS), lon=round(float(r["lon"]), PLACE_DECIMALS),
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
        raise SetupError("Couldn't reach OpenStreetMap. Check the connection and run it again.") from None
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
                         "Run it again, or pass --address.")
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
        tz = get(OPEN_METEO_FORECAST, {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1}).get("timezone")
    except (requests.RequestException, ValueError, AttributeError):
        tz = None
    if not valid_timezone(tz):
        raise SetupError("Couldn't get the timezone from Open-Meteo. Run it again, or pass --timezone "
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
            place.lat, place.lon = round(args.lat, PLACE_DECIMALS), round(args.lon, PLACE_DECIMALS)
            place.found_by += "; coordinates yours"
    else:
        place = find_coordinates(round(args.lat, PLACE_DECIMALS), round(args.lon, PLACE_DECIMALS), get)
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
    alone), "area" or "model". One area request on AirGradient's public map around the home."""
    try:
        world = publicmap.fetch_area(place.lat, place.lon, publicmap.AREA_KM + 1)
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


NO_MONITOR = {None: "No AirGradient monitor answered on this network",
              "wsl": "WSL can't see your Wi-Fi's monitors",
              "container": "a container can't see your Wi-Fi's monitors",
              "hosted": "a hosted copy can't see your Wi-Fi's monitors"}   # by localnet.environment_blocker(): why nothing was found
KINDS = ("indoor", "outdoor")
HOME_SERVER_CMD = f"{SETUP_CMD} --home-server"


def also_found_line(kind, item):
    """The line naming a monitor of a kind already read, and the command that reads it instead."""
    return (f"Also found: another {kind} monitor (…{item.serial[-4:]}, {item.model}). "
            f"To read it instead: {SETUP_CMD} --monitor {item.serial}")


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
        try:
            row = localnet.map_reading(reader(None, item.address, item.port), mon, datetime.now(timezone.utc))
        except Exception:   # not answering, or not a monitor
            row = None
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
                  f"Add it to the account in AirGradient's dashboard first.")
            return 1
    print(f"{kind}: {serial} ({item.model}), read directly from now on")
    print(f"Settings: {write_found(root, chosen, monitor_settings(chosen))}")
    return start_app(env, root)


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
        if not any(item.serial in known for item in found):   # a monitor already set that missed one read stays set
            lines.append("Connect AirGradient on the page instead.")
        if blocker == "container":
            lines.append(f"The token is entered on the computer that ran setup; then run {HOME_SERVER_CMD} again.")
    print("\n".join(lines))
    if chosen or not (root / "config.toml").exists():
        print(f"Settings: {write_found(root, chosen, unit_country)}")
    return 0


def setup(args, env, root):
    prepare_env(root)
    profiles = [x for x in re.split(r"[,\s]+", args.profiles.lower()) if x and x != "none"]
    if any(p not in PROFILES for p in profiles):
        raise SetupError(f"Choose profiles from: {', '.join(PROFILES)}, or none. See docs/optional.md for adding one.", 2)
    token = env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root))
    if not token:
        raise SetupError(f"Connect AirGradient first: open the private link {CONNECT_CMD} prints, or fill AIRGRADIENT_TOKEN in .env and restart.")
    rows = account_rows(token)
    monitors = choose_monitors(rows, args)
    place = resolve_place(args)
    outdoor = outdoor_source(place) if "outdoor" not in monitors else None
    path = root / "config.toml"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    text = config.set_monitor_keys(build_config(place, monitors, profiles, outdoor), kept_monitor_keys(old, monitors))
    cfg = load_config({"CONFIG_TOML": text}, root)
    if old != text:
        if old is not None:
            backup = config.backup_config(path)
            print(f"Saved the previous config at {backup}")
        path.write_text(text, encoding="utf-8")
    print(f"Home: {place.name}, {place.lat}, {place.lon}; timezone {place.timezone}")
    print(f"Profiles: {', '.join(profiles) or 'none'}")
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
        print(f'{kind} "{getattr(cfg.place, kind)}", {words}')
        if age is None or age > publicmap.STALE_READING:
            problems.append(f'The {kind} monitor has no recent reading. Check that it is plugged in and online.')
    for problem in problems:
        print(problem)
    print("Not live yet: fix the readings above and run setup again." if problems else "Live OK")
    return 1 if problems else 0


NO_WEB_WITHOUT_TOKEN = "A copy on the web can't see your Wi-Fi: connect AirGradient on this computer's page first."


def railway(service, root):
    keys = saved_keys(root)
    if not env_key("AIRGRADIENT_TOKEN", keys):
        raise SetupError(NO_WEB_WITHOUT_TOKEN)
    # a web copy reads the cloud only: the monitors' addresses (a home-server copy's) never leave this computer
    text = config.set_monitor_keys((root / "config.toml").read_text(encoding="utf-8"), {k: {f"{k}_host": None} for k in KINDS})
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
        print(f"The page doesn't answer at {base}. Start it (SETUP.md, Start and verify), or pass its address: "
              "setup.py --live http://localhost:<port>")
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
        problem("the page runs the demo (made-up data). Restart it from the folder with config.toml.")
    elif cfg is not None:
        settings_check(app, cfg, problem)

    blocked = blocked_by_macos(health, cfg, base, env, root, reach or localnet.reach, platform)
    where = monitors_where(health, cfg, problem, blocked)
    if token and (where is None or any(w != "local" for w in where.values())):
        current_check(get, base, cfg, now, problem)
    all_local = bool(where) and all(w == "local" for w in where.values())
    if all_local and not token:
        print("Note: optional: charts start from now")
    if problems:
        print("Not live yet: fix the problems above, then run setup.py --live again.")
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
        label = f'{kind} "{publicmap.plain_text(name)}"' if name else kind
        print(f"{label}: {localnet.WHERE[state]}")
        if state == "stale" and not blocked:
            problem(f"the {kind} monitor is {localnet.WHERE['stale']}: is it plugged in and on this Wi-Fi?")
        out[kind] = state
    if blocked:
        line = (f"the monitors answer on this Wi-Fi, but Home Air doesn't read them: macOS's Local Network "
                f"permission is likely off for {blocked}. In {LOCAL_NETWORK}, turn it on for {blocked}, then run "
                f"{SETUP_CMD} --live again.")
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
        problem(f"the page's {k} is {json.dumps(shown[k])}, config.toml's is {json.dumps(wanted[k])}. Restart the "
                "server: it reads config.toml when it starts.")
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
        problem(f"the page has no token yet. Open the private link {CONNECT_CMD} prints, or fill AIRGRADIENT_TOKEN in .env and restart.")
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
    """What's done and the one next command, as SETUP.md names it. Read-only: writes nothing, asks nothing; its one
    network request (the account's latest readings) is a GET. Keys are reported as set or not, never their values."""
    env = {k: v for k, v in env.items() if k != "DEMO"}
    rows, next_cmd = [], None

    def step(name, done, words, cmd):
        nonlocal next_cmd
        rows.append(f"  {name}: {'done' if done else 'not yet'}" + (f": {words}" if words else ""))
        if not done and next_cmd is None:
            next_cmd = cmd

    try:
        text = (root / ".env").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    token_set = check_config.keys_present(env, root)["AIRGRADIENT_TOKEN"]
    prepared = token_set or any(defines(line, "AIRGRADIENT_TOKEN") for line in text.splitlines())
    step("1. Token entry (.env)", prepared, "" if prepared else "no AIRGRADIENT_TOKEN line yet", SETUP_CMD)
    step("2. AirGradient connected", token_set, "" if token_set else "no token yet",
         f"{START_CMD} (then open the private link it prints; the user enters the token on that page)")

    cfg, problem = None, None
    # where load_config reads the home from, in its order: the variables win over the file
    source = next((k for k in ("CONFIG_TOML", "CONFIG_FILE") if env.get(k, "").strip()), "config.toml")
    try:
        cfg = load_config(env, root)
    except ConfigError as e:
        problem = str(e)
    if cfg is not None:
        p = cfg.place
        monitors = [f'{kind} "{getattr(p, kind)}"' for kind in ("indoor", "outdoor") if getattr(p, kind) is not None]
        profiles = ", ".join(k for k, v in cfg.profiles.items() if v) or "none"
        words = f"{p.name}; monitors: {', '.join(monitors)}; profiles: {profiles}"
    elif problem and source == "config.toml":
        words = f"{problem}. Running setup again replaces it and keeps a copy"
    elif problem:
        words = f"{problem}. Fix the {source} variable: it wins over config.toml"
    else:
        words = "no config.toml yet"
    fix = HOME_CMD if source == "config.toml" else f"{RUN} scripts/check_config.py"
    step(f"3. Home and monitors ({source})", cfg is not None, words, fix)

    health = running_health(env, root) if cfg is not None else None
    if cfg is not None and health is not None and isinstance(health.get("monitors"), dict) and health["monitors"]:
        words = "; ".join(f'{k} "{getattr(cfg.place, k)}": {localnet.WHERE.get(v, localnet.WHERE["stale"])}' if getattr(cfg.place, k, None)
                          else f"{k}: {localnet.WHERE.get(v, localnet.WHERE['stale'])}" for k, v in health["monitors"].items())
        bad = any(v not in ("local", "cloud") for v in health["monitors"].values())
        step("4. Readings come in", not bad, words, f"check the monitor is plugged in and on this Wi-Fi, then run {STATUS_CMD}")
    elif cfg is not None and not token_set:   # the token is optional: with the page not running there is nothing to read
        rows.append("  4. Readings come in: not checked: the app isn't running")
    elif not (token_set and cfg is not None):
        rows.append("  4. Readings come in: not checked yet (needs the steps above)")
    else:
        token = env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root))
        try:
            account = account_rows(token, timeout=STATUS_TIMEOUT)
        except AirGradientSlow:
            step("4. Readings come in", False, f"couldn't check: AirGradient didn't answer within {STATUS_TIMEOUT} s; try again in a minute", STATUS_CMD)
        except TokenRefused:
            step("4. Readings come in", False, "AirGradient refused the token",
                 f"{START_CMD} (then open the private link it prints and enter the token again)")
        except SetupError:
            step("4. Readings come in", False, "couldn't check: AirGradient's answer couldn't be read; try again in a minute", STATUS_CMD)
        else:
            seen, missing, stale = [], [], []
            for kind in ("indoor", "outdoor"):
                name, wanted = getattr(cfg.place, kind), getattr(cfg.place, kind + "_id")
                if name is None:
                    continue
                row = next((r for r in account if (r.get("locationId") == wanted if wanted is not None
                                                   else r.get("locationType") == kind)), None)
                if row is None:
                    missing.append(f'the {kind} monitor "{name}" isn\'t in this account')
                    continue
                age, when = age_words(row.get("timestamp"), datetime.now(timezone.utc))
                seen.append(f'{kind} "{name}", {when}')
                if age is None or age > publicmap.STALE_READING:
                    stale.append(f'the {kind} monitor "{name}" has no recent reading')
            if missing:
                step("4. Readings come in", False, "; ".join(missing + stale),
                     f"run setup again to choose from this account's monitors: {HOME_CMD}")
            else:
                step("4. Readings come in", not stale, "; ".join(stale or seen),
                     f"check the monitor is plugged in and shows readings in the AirGradient app, then run {STATUS_CMD}")

    print("Setup status:")
    for row in rows:
        print(row)
    if next_cmd is not None:
        print(f"Next: {next_cmd}")
        return 0
    # Done is SETUP.md's "Done when": the running page's --live check ends with Live OK, which needs the page started.
    port = port_from(env)
    start = f"cd {root} && {'PORT=' + str(port) + ' ' if port != DEFAULT_PORT else ''}{START_CMD}"
    print("Steps 1–4 done. Last, check the running page: it should end with Live OK.")
    print(f"Page: http://localhost:{port}")
    print(f"Next: {start}, then {SETUP_CMD} --live http://localhost:{port} "
          "(Railway or Docker: --live <the page's address>)")
    return 0


def page_answers(url, env, root):
    """After the system starts the app: wait for /health, then the --live check."""
    end = time.monotonic() + STARTUP_WAIT
    while time.monotonic() < end:
        try:
            if requests.get(f"{url}/health", timeout=WAIT_POLL_SECONDS).ok:
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
        print(f"The copy that starts at login didn't restart. Its log: app.log. Set it up again: {SETUP_CMD} --start-at-login on")
        return 1
    end = time.monotonic() + STARTUP_WAIT
    while time.monotonic() < end:
        now = pid_file.read_text().strip() if pid_file.exists() else None
        if now and now != before and page_up(port):
            break
        time.sleep(1)
    else:
        print(f"The copy that starts at login isn't answering yet at http://localhost:{port}. See app.log, then run {START_CMD} again.")
        return 1
    (root / PORT_FILE).write_text(f"{port}\n")
    print(f"Restarted: http://localhost:{port}, the copy that starts at login (log: app.log)")
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


def has_saved_token(env, root):
    return bool(env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root)))


def private_link(port, root):
    """The private link to the token box: the owner's code (token_setup.owner_code, kept in connect-code) sets the
    owner cookie in the browser that opens it, for a year."""
    return f"http://localhost:{port}/connect?code={token_setup.owner_code(root / token_setup.CODE_FILE)}"


def print_connect_link(port, root):
    """Printed here for the agent to open, never by the app, whose output goes to app.log."""
    print(f"Connect AirGradient here (a private link: don't share it): {private_link(port, root)}")


def connect_link(env, root):
    """--connect: the private link again, for the copy --start started (or PORT, or 5555)."""
    port = started_port(env, root)
    if has_saved_token(env, root):
        print(f"A token is already saved: nothing to connect. The page: http://localhost:{port}")
        return 0
    print_connect_link(port, root)
    return 0


def start_at_login(state, env, root):
    import autostart
    run = lambda cmd: subprocess.run(cmd, capture_output=True).returncode
    home = Path(env.get("HOME") or Path.home())
    if state == "off":
        return autostart.turn_off(root, home, run, shutil.which, sys.platform)
    url = f"http://localhost:{port_from(env)}"
    code = autostart.turn_on(root, home, env, run, shutil.which, sys.platform, lambda: page_answers(url, env, root))
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
            print(f"{', '.join(kinds).capitalize()}: read on your Wi-Fi by the copy that starts at login.")
            return True
        waited = clock() - start
        if waited >= LOGIN_READ_WAIT:
            print(f"No reading on your Wi-Fi after {LOGIN_READ_WAIT} s. If macOS asked about \"uv\", it needs Allow: "
                  f'{LOCAL_NETWORK}, turn on "uv", then run {SETUP_CMD} --live.')
            return False
        if not told and waited >= LOGIN_READ_QUIET:
            print(f'Waiting for the first reading on your Wi-Fi (up to {LOGIN_READ_WAIT} s): press Allow on macOS\'s '
                  'prompt "Allow "uv" to find devices on local networks?".', flush=True)
            told = True
        sleep(WAIT_POLL_SECONDS)


def running_agent(env):
    """The coding agent running setup, from the variables it sets for its commands; None when it can't be told. Only
    picks the folders to link: the message names an agent only when it's certain (Claude Code's CLAUDECODE=1)."""
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
    setup, or when the agent can't be told and this computer has Claude Code (~/.claude)."""
    agent = running_agent(env)
    targets = [home / SHARED_SKILLS / SKILL]
    if agent == "Claude Code" or (agent is None and (home / ".claude").is_dir()):
        targets.append(home / CLAUDE_SKILLS / SKILL)
    return agent, targets


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
        print(f"Home Air skill ({SKILL}):")
        for p in paths:
            if links_to(p, source):
                words = "linked by setup" if str(p) in record["links"] else "linked to this folder, not by setup (--agent off leaves it)"
            elif p.is_symlink() or p.exists():
                words = "a different skill with this name (setup leaves it)"
            else:
                words = "not linked"
            print(f"  {short(p)}: {words}")
        return 0
    if state == "off":
        if not record["links"]:
            print("Setup hasn't linked the Home Air skill anywhere: nothing to undo.")
            return 0
        for p in map(Path, record["links"]):
            if p.is_symlink() and os.readlink(p) == record["skill"]:
                p.unlink()
                print(f"Removed {short(p)}")
            elif p.is_symlink() or p.exists():
                print(f"Left {short(p)}: it's no longer the link setup made")
        (root / AGENT_FILE).unlink(missing_ok=True)
        print("The Home Air skill is removed from your agent.")
        return 0
    if not (source / "SKILL.md").is_file():
        raise SetupError(f"This Home Air folder has no skills/{SKILL}. Update it with git pull, then run {SETUP_CMD} --agent on again.")
    agent, targets = agent_targets(env, home)
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
        print(f"Left {short(p)}: a different skill named {SKILL} is already there. Setup doesn't replace it.")
    if not linked:
        print("The skill wasn't added: see above." + (" Remove or rename that skill, then run this again." if foreign else ""))
        return 1
    where = " and ".join(f"{short(p)} (read by {FOR_AGENTS.get(p.parent.relative_to(home).as_posix(), 'an agent')})"
                         for p in linked)   # every target is under home (agent_targets)
    print(f"Added the Home Air skill to {'Claude Code' if agent == 'Claude Code' else 'your agent'}: linked this folder's "
          f"skills/{SKILL} into {where}.")
    print("With it, the agent answers from your monitors, and changes your settings only when you ask and say yes.")
    print("Ask it about the air at home, in a new session if this one doesn't see the skill yet. "
          "The first time, the agent asks before running the skill's script.")
    print(f"Undo: {AGENT_OFF_CMD}")
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
    if start_at_login("on", env, root) != 0 and not page_up(started_port(env, root)):
        code = start_app(env, root)   # turning it on stopped the copy started above: bring it back for this session
        if code:
            return code
    agent_skill("on", env, root)   # says why when it can't link; the home works without it
    port = started_port(env, root)
    url = f"http://localhost:{port}"
    # Without a token, the private link: this browser gets the owner cookie, so the token box opens later from the page
    # (in "local" mode the link then shows the home, with the box at /connect; with nothing found it shows the box).
    open_page(url if has_saved_token(env, root) else private_link(port, root), env)
    if not has_saved_token(env, root) and not known_serials(root):
        print(f"Nothing is read yet. The AirGradient token is optional (it adds the last 7 days and putting it online): "
              f"enter it on the page at {url}, or in .env, then run {SETUP_CMD} again.")
        return 0
    if has_saved_token(env, root):
        try:
            print(connected_line(account_rows(env_key("AIRGRADIENT_TOKEN", env) or env_key("AIRGRADIENT_TOKEN", saved_keys(root)))))
        except SetupError as error:
            print(error)
    return live(url, env, root, wait=LIVE_RETRY_SECONDS)


def parser():
    ap = argparse.ArgumentParser(description="Set up a home from its location and profiles. With no answers, prepare .env.")
    ap.add_argument("--address", help="an address, neighborhood or nearby landmark with its city")
    ap.add_argument("--profiles", default="none", help="toddler,sinus,runner, or none (default)")
    ap.add_argument("--indoor-id", type=int, help="which indoor monitor, only when ambiguous")
    ap.add_argument("--outdoor-id", type=int, help="which outdoor monitor, only when ambiguous")
    ap.add_argument("--monitor", metavar="SERIAL",
                    help="read this monitor found on the Wi-Fi instead of the one of its kind read now")
    ap.add_argument("--name", help="a correction to the area's name")
    ap.add_argument("--lat", type=float, help="latitude, together with --lon")
    ap.add_argument("--lon", type=float, help="longitude, together with --lat")
    ap.add_argument("--zip", help="a correction to a US ZIP code")
    ap.add_argument("--region", help="a correction to the city or region")
    ap.add_argument("--timezone", help="a correction using an IANA timezone name")
    ap.add_argument("--live", nargs="?", const="", metavar="URL", help="check the running page with GET requests")
    ap.add_argument("--status", action="store_true", help="list what's done and the next command; changes nothing")
    ap.add_argument("--start", action="store_true", help="start the app in the background and wait until it answers")
    ap.add_argument("--connect", action="store_true", help="print the private link to the token box (no token saved yet)")
    ap.add_argument("--start-at-login", choices=("on", "off"),
                    help="start the app by itself at login (a Mac or Linux with systemd), or stop doing so")
    ap.add_argument("--home-server", action="store_true",
                    help="prepare a folder for a home server with Docker (Compose, settings, token) and say the two steps")
    ap.add_argument("--agent", choices=("on", "off", "status"),
                    help="connect the agent running setup to this Home Air (a skill, linked), undo it, or say what's linked")
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
        if args.home_server:
            import homeserver
            return homeserver.prepare(root)
        if args.start:
            return start_app(env, root)
        if args.connect:
            return connect_link(env, root)
        if args.start_at_login:
            return start_at_login(args.start_at_login, env, root)
        if args.agent:
            return agent_skill(args.agent, env, root)
        if args.live is not None:
            return live(args.live or f"http://localhost:{started_port(env, root)}", env, root, wait=LIVE_RETRY_SECONDS)
        if not args.address and args.lat is None and args.lon is None:
            return one_run(env, root)
        return setup(args, env, root)
    except (SetupError, ConfigError, OSError, UnicodeError) as error:
        print(str(error) if isinstance(error, (SetupError, ConfigError)) else "Couldn't read or write a setup file. Check its permissions.")
        return error.code if isinstance(error, SetupError) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
