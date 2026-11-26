"""Checks a config file before the app starts. Prints plain problems; reports keys as set or missing, never their values.
For a home without an outdoor monitor it also checks the chosen outdoor source answers (one request to AirGradient's
public map), and --suggest lists the nearby public outdoor monitors and prints the source to choose.

Usage: ./homeair check --config, ./homeair check --suggest, or ./homeair check (this, then the running page);
directly, python scripts/check_config.py [--file PATH] [--suggest]
"""
import argparse
import os
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DEFAULT_LIMITS, parse_dotenv, OLD_PROFILES_TABLE, PROFILES, ConfigError, _clean, limit_order_problems, load_config, located, real_value, route_path  # noqa: E402
import publicmap  # noqa: E402
from publicmap import AREA_KM, AREA_MIN_MONITORS, NEARBY_SOURCE_KM, PINNED_OFFLINE_DAYS  # noqa: E402

KEYS = ("AIRGRADIENT_TOKEN", "GOOGLE_POLLEN_API_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY")
OUTDOOR_FROM_NEEDED = "sources.outdoor_from is needed"   # config.py's words when an indoor-only home hasn't chosen yet
SUGGEST_LIST = 5   # monitors --suggest lists before its choice: the nearest reporting within AREA_KM, enough to compare


def keys_present(env, root: Path) -> dict[str, bool]:
    """True per key if it has a real value where the server takes it from: the environment first, else .env's first
    line for that key (config.parse_dotenv, the server's own reader). Values are never kept or printed."""
    try:
        file = parse_dotenv((root / ".env").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        file = {}
    return {k: real_value(env[k] if k in env else file.get(k, "")) for k in KEYS}


def decimals(x: float) -> int:
    s = repr(x)
    return len(s.split(".")[1]) if "." in s and "e" not in s else 0


def profiles_table(raw: dict) -> str:
    """The table the app reads the profiles from: [profiles], else its old name [needs] (as config.load_config does)."""
    return "profiles" if "profiles" in raw or OLD_PROFILES_TABLE not in raw else OLD_PROFILES_TABLE


def old_name_note(raw: dict) -> str | None:
    """One line when the config still uses [needs], the old name of [profiles]. Not a problem: it still works."""
    if OLD_PROFILES_TABLE not in raw:
        return None
    if "profiles" in raw:
        return f"Note: [{OLD_PROFILES_TABLE}] is the old name of [profiles]. Both are here, so [{OLD_PROFILES_TABLE}] is ignored: delete it."
    return f"Note: [{OLD_PROFILES_TABLE}] is the old name of [profiles]. It still works; rename it to [profiles]."


def extra_problems(raw: dict, root: Path, online: bool = False) -> list[str]:
    """online: the settings come from CONFIG_TOML, as on Railway. There the exact location must not be: config.toml on
    this computer keeps it (for the roads) and ./homeair railway sends it rounded; the app rounds it everywhere."""
    out = []
    place = raw.get("place") if isinstance(raw.get("place"), dict) else {}
    for key, label in (("lat", "latitude"), ("lon", "longitude")):
        v = place.get(key)
        if online and isinstance(v, float) and decimals(v) > 2:
            out.append(f"{label} has {decimals(v)} decimals: round to 2, about 1 km: the exact location stays on the computer that ran setup")
    table = profiles_table(raw)
    profiles = raw.get(table) if isinstance(raw.get(table), dict) else {}
    for k in profiles:
        if k not in PROFILES:
            out.append(f"{table}.{k} is not a profile the page knows. Use only: {', '.join(PROFILES)}")
    limits = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}

    numbers = {k: v for k, v in limits.items() if k in DEFAULT_LIMITS and isinstance(v, (int, float)) and not isinstance(v, bool)}
    if len(numbers) == len([k for k in limits if k in DEFAULT_LIMITS]):   # a non-number is load_config's to report
        out += limit_order_problems(numbers)
    road = raw.get("road")
    if isinstance(road, dict) and isinstance(road.get("route"), str):
        if not route_path(road["route"], root).is_file():
            out.append(f"road.route file not found: {road['route']}")
    return out


def outdoor_source_line(cfg) -> str:
    """Which monitors the home has, and where the outdoor PM2.5 comes from without one. Static: no network."""
    p = cfg.place
    if p.indoor is None and p.outdoor is None:
        return "Monitors: none yet"
    if p.indoor is not None and p.outdoor is not None:
        return "Monitors: indoor and outdoor"
    if p.outdoor is not None:
        return "Monitors: outdoor only"
    of = cfg.outdoor_from
    src = f"public monitor {of}" if isinstance(of, int) else f"the {of} estimate"
    return f"Monitors: indoor only; outdoor PM2.5 from {src}"


def outdoor_from_note(raw: dict) -> str | None:
    """One line when outdoor_from is set next to an outdoor monitor: it is ignored."""
    sources = raw.get("sources") if isinstance(raw.get("sources"), dict) else {}
    place = raw.get("place") if isinstance(raw.get("place"), dict) else {}
    if "outdoor_from" in sources and str(place.get("outdoor", "")).strip():
        return "Note: sources.outdoor_from is ignored while place.outdoor names a monitor: delete one of them."
    return None


def _monitors(n: int) -> str:
    return f"{n} public outdoor monitor{'' if n == 1 else 's'}"


def suggest_line(world, lat: float, lon: float, now, skip=None) -> str:
    """The outdoor source to choose at a place, and why in one line (publicmap.suggest: only monitors reporting now, by
    the server's rule). `skip`: a monitor id to leave out (the chosen one, when looking for the next best)."""
    if skip is not None:
        world = [m for m in world if not (isinstance(m, dict) and m.get("locationId") == skip)]
    choice, first, count = publicmap.suggest(world, lat, lon, now)
    if isinstance(choice, int):
        km, mon = first
        return (f"Suggested outdoor_from = {choice}: {publicmap.name_of(mon)}, {km:.1f} km away, the nearest public outdoor "
                f"monitor reporting now (within {NEARBY_SOURCE_KM} km, so its readings are close to your street's air).")
    if choice == "area":
        return (f'Suggested outdoor_from = "area": no public outdoor monitor within {NEARBY_SOURCE_KM} km; '
                f"{_monitors(count)} within {AREA_KM} km tune the forecast to your area.")
    return (f'Suggested outdoor_from = "model": {_monitors(count) if count else "no public outdoor monitor"} within '
            f"{AREA_KM} km, fewer than {AREA_MIN_MONITORS} for an area estimate, so Open-Meteo's model alone.")


def suggest_list(world, lat: float, lon: float, now) -> list[str]:
    """The public outdoor monitors --suggest weighed: up to SUGGEST_LIST reporting now within AREA_KM (the server's
    rule, publicmap.within), nearest first, each with its distance, last corrected PM2.5 and id. None reporting: no lines."""
    near = publicmap.within(world, lat, lon, AREA_KM, now)
    if not near:
        return []
    more = f" (the {SUGGEST_LIST} nearest of {len(near)})" if len(near) > SUGGEST_LIST else ""
    lines = [f"Public outdoor monitors reporting now within {AREA_KM} km, nearest first{more}:"]
    for km, mon in near[:SUGGEST_LIST]:
        pm = publicmap.reading_row(mon).get("pm02_corrected")
        reading = f"corrected PM2.5 {pm:.1f} µg/m³" if pm is not None else "no corrected PM2.5 (no humidity reading)"
        lines.append(f"  {publicmap.name_of(mon)}, {km:.1f} km, {reading}, id {mon.get('locationId')}")
    return lines


def _when(ts):
    """A monitor's timestamp as an aware datetime, or None."""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def source_lines(cfg, world, now) -> tuple[list[str], list[str]]:
    """(lines, problems) on an indoor-only home's chosen outdoor source against the map's monitors. A source that isn't
    reporting is a note, not a problem: the page falls back to an estimate until it returns."""
    of, lat, lon = cfg.outdoor_from, cfg.place.lat, cfg.place.lon
    if of == "area":
        n = len(publicmap.within(world, lat, lon, AREA_KM, now))   # reporting now, as the server counts them
        if n >= AREA_MIN_MONITORS:
            return [f"Outdoor source: the area estimate, from {_monitors(n)} within {AREA_KM} km."], []
        return [f"Note: only {_monitors(n)} within {AREA_KM} km, fewer than {AREA_MIN_MONITORS}: the page uses "
                "Open-Meteo's model alone until more report."], []
    if not isinstance(of, int):
        return [], []
    listed = next((m for m in world if isinstance(m, dict) and m.get("locationId") == of), None)
    if listed is not None and publicmap.find(world, of) is None:
        if not str(listed.get("model") or "").startswith(publicmap.OUTDOOR_PREFIX):
            return [], [f"sources.outdoor_from: public monitor {of} is not an outdoor monitor; choose an outdoor one (--suggest)"]
        return [f"Note: public monitor {of} has no position on AirGradient's public map, so the app can't place it or "
                "find its history: the page shows an estimate for your area until it has one. The next best: "
                + suggest_line(world, lat, lon, now, skip=of)], []
    if listed is None:
        return [f"Note: public monitor {of} is not on AirGradient's public map (the id is its map id): the page shows "
                "an estimate for your area until it is. If it stays gone, the next best: "
                + suggest_line(world, lat, lon, now, skip=of)], []
    name, km = publicmap.name_of(listed), publicmap.km_between(lat, lon, listed["latitude"], listed["longitude"])
    last = _when(listed.get("timestamp"))
    if last is not None and now - last >= timedelta(days=PINNED_OFFLINE_DAYS):
        return [f"Note: public monitor {of} ({name}) has not reported for {(now - last).days} days, since "
                f"{last.date().isoformat()}. The next best: " + suggest_line(world, lat, lon, now, skip=of)], []
    if not publicmap.reporting(listed, now):   # offline, or older than publicmap.STALE_READING: the server's rule
        return [f"Note: public monitor {of} ({name}) is not reporting now: the page shows an estimate for your area "
                "until it returns."], []
    return [f"Outdoor source: public monitor {of} ({name}, {km:.1f} km away) is reporting."], []


def main(argv, env, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description="Check config.toml before starting the app.")
    ap.add_argument("--file", help="config file to check (default: CONFIG_FILE, else config.toml, else CONFIG_TOML)")
    ap.add_argument("--suggest", action="store_true",
                    help="list the nearby public outdoor monitors and print the source to choose without an outdoor monitor "
                         "(one area request to AirGradient's public map)")
    args = ap.parse_args(argv)

    if args.file:
        path = Path(args.file)
        label = path.name
    elif env.get("CONFIG_FILE", "").strip():
        path = Path(env["CONFIG_FILE"].strip())
        label = path.name
    elif (root / "config.toml").is_file():
        path = root / "config.toml"
        label = "config.toml"
    else:
        path, label = None, "CONFIG_TOML"
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"Problem: cannot read {path}: {e}")
            return 1
    elif env.get("CONFIG_TOML", "").strip():
        text = env["CONFIG_TOML"]
    else:
        print("Problem: No config.toml found. Run ./homeair setup to write one, or copy config.example.toml to "
              "config.toml and edit the copy.")
        return 1
    print(f"Checked {args.file if args.file else label}")

    problems = []
    try:
        raw = tomllib.loads(_clean(text))
    except tomllib.TOMLDecodeError as e:
        print(f"Problem: {label}: {e}")
        return 1
    problems += extra_problems(raw, root, online=not args.file and label == "CONFIG_TOML")
    cfg = None
    try:
        cfg = load_config({"CONFIG_TOML": text}, root)
    except ConfigError as e:
        msg = str(e).replace("CONFIG_TOML", label, 1)
        if "cannot read road.route" in msg and any("road.route file not found" in p for p in problems):
            pass
        elif not any(p in msg for p in problems):   # load_config stops at the first problem already listed above
            problems.append(msg)

    keys = keys_present(env, root)
    for k, ok in keys.items():
        print(f"{k} {'set' if ok else 'missing'}")
    if cfg:
        print(outdoor_source_line(cfg))
        if not located(cfg):
            print("Place: not set yet (weather, pollen, smoke and nearby monitors need it)")
    for note in (old_name_note(raw), outdoor_from_note(raw)):
        if note:
            print(note)
    check_source = located(cfg) and cfg.place.outdoor is None and cfg.outdoor_from != "model"
    if check_source or args.suggest:
        lines, more = outdoor_source_check(cfg if check_source else None, raw, args.suggest)
        for line in lines:
            print(line)
        problems += more
    pending = []
    if args.suggest:   # --suggest is how the outdoor source gets chosen: its absence is the next step, not a problem
        pending = [p for p in problems if OUTDOOR_FROM_NEEDED in p]
        problems = [p for p in problems if OUTDOOR_FROM_NEEDED not in p]
        if pending:
            print("Still to choose: sources.outdoor_from (write the user's pick, then run ./homeair check --config)")
    for p in problems:
        print(f"Problem: {p}")
    if problems:
        return 1
    if args.suggest and pending:
        return 0   # not "Config OK" yet: the source is still to write
    print("Config OK")
    if not keys.get("AIRGRADIENT_TOKEN"):
        print(token_note(cfg))
    return 0


def token_note(cfg) -> str:
    """The line after Config OK when no token is set: the token is optional for monitors read on the Wi-Fi by serial."""
    p = cfg.place
    named = [(p.indoor, p.indoor_serial), (p.outdoor, p.outdoor_serial)]
    named = [serial for name, serial in named if name is not None]
    if not named:
        return "Not live yet: connect AirGradient on the page"
    if all(named):
        return "Optional: the AirGradient API token adds the last 7 days (charts start from now)"
    return "Not live yet: add your AirGradient API token to .env in your text editor, then restart Home Air (./homeair start)."


def outdoor_source_check(cfg, raw: dict, suggest: bool) -> tuple[list[str], list[str]]:
    """(lines, problems): the chosen source checked (cfg: an indoor-only home's config, or None) and, with suggest,
    the source to choose at place.lat/lon. One area request on AirGradient's public map around the place (and the
    chosen monitor by its id when it isn't in that area); without an answer, a note."""
    place = raw.get("place") if isinstance(raw.get("place"), dict) else {}
    lat, lon = (cfg.place.lat, cfg.place.lon) if cfg else (place.get("lat"), place.get("lon"))
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon)):
        return ["Note: --suggest needs place.lat and place.lon."] if suggest else [], []
    try:
        world = publicmap.fetch_area(lat, lon, AREA_KM + 1)
        of = cfg.outdoor_from if cfg else None
        if isinstance(of, int) and publicmap.find(world, of) is None:
            try:
                world = world + [publicmap.monitor_row(publicmap.fetch_place(of), publicmap.fetch_now(of))]
            except Exception:   # not on the map (or no answer for it): source_lines says it isn't listed
                pass
    except Exception as e:   # no network, AirGradient down, bad JSON: say so, the config itself may be fine
        return [f"Note: AirGradient's public map didn't answer ({type(e).__name__}), so the outdoor source wasn't checked."], []
    world = world if isinstance(world, list) else []
    now = datetime.now(timezone.utc)
    lines, problems = source_lines(cfg, world, now) if cfg else ([], [])
    if suggest:
        lines += suggest_list(world, lat, lon, now) + [suggest_line(world, lat, lon, now)]
    return lines, problems


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
