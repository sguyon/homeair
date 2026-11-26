"""Home Air: the Flask app, page, routes, config loading and startup."""

import base64
import gzip
import hashlib
import importlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, request, send_file, make_response, redirect, render_template

import budgets
import phrases
import road_data
import roads as roads_mod
import token_setup
import upstream
from alert_rules import OFFLINE_AFTER, QUIET_DEFAULT
import summary as summary_rules
from config import (parse_dotenv, APPLIANCES, DEFAULT_LIMITS, ROAD_FOUND, ConfigError, add_key, backup_config, config_path, env_key,
                    load_config, public_view, monitors as config_monitors, real_value, replace_text, route_path,
                    set_monitor_keys, set_road_on, write_env_key)

# Load .env file if it exists
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for key, val in parse_dotenv(env_path.read_text()).items():
        os.environ.setdefault(key, val)

# Reloading server has always reset its cache, pools, and demo replacements.
# Reset the moved state too, before applying this startup's config and mode.
for module_name in ("sources", "air_read"):
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
import sources
import air_read

sources.runtime = air_read.runtime = sys.modules[__name__]

_CONTEXT_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="context")   # /api/context fan-out; separate from the pollen deadline pool
TOKEN = env_key("AIRGRADIENT_TOKEN")   # .env.example's placeholder counts as no token
# what the server prints when it stops for want of a config (a token but no home: it won't guess one)
NO_CONFIG_MESSAGE = "No config: copy config.example.toml to config.toml (or set CONFIG_TOML)"
APP_LOAD_ERROR = 4   # gunicorn's exit code for "the app can't load": the master stops instead of respawning forever
ENV_LABEL = {"dev": "DEV", "preview": "PREVIEW"}   # the tag on the page title and app name on dev and preview
# Addresses that only reach this machine or the home's own network (for the DEV tag): private IPv4 ranges, Tailscale's
# 100.64.0.0/10 range, and names that never resolve on the internet: bare machine names, Bonjour's .local, Tailscale's ts.net
LOCAL_HOST = re.compile(r"^(localhost|0\.0\.0\.0|127\.[\d.]+|10\.[\d.]+|192\.168\.[\d.]+|172\.(1[6-9]|2\d|3[01])\.[\d.]+"
                        r"|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.[\d.]+|[a-z0-9-]+|[a-z0-9.-]+\.(local|ts\.net))$")
# The weather card's words. Dew point (°C) under DEW_DRY_C dries sinuses (the Open-Meteo source note says so), under
# DEW_SEVERE_DRY_C badly. Visibility (m) under each line reads very low, reduced, moderate; above the last, clear.
DEW_SEVERE_DRY_C, DEW_DRY_C = 5, 10
VISIBILITY_VERY_LOW_M, VISIBILITY_REDUCED_M, VISIBILITY_MODERATE_M = 2000, 5000, 10000
# digits: the longest ?monitor= read as an id (AirGradient's are 5-6 digits today); a longer one is junk, and past
# about 4,300 digits Python's int() raises instead of reading it
MONITOR_ID_MAX_DIGITS = 12
APP_VERSION = upstream.APP_VERSION   # the release shown on the page's build line (and the dev bar), and in the User-Agent
SAVED_NAMES_SHOWN = 4   # monitor names listed on the "connected" card; more become "and N more"


def _note_cache(info):
    try:
        g.cache_state, g.cache_age = info["state"], int(info["age"])
    except RuntimeError:
        pass


app = Flask(__name__)
token_state = token_setup.TokenState()


def roads_status(cfg):
    """Why "Around you" lists the roads it lists: "ok" (roads found at setup), "manual" (a hand-set road only), "failed"
    (the road data wasn't fetched: the pending marker), "not_built" (no road data for the area yet) or "none" (no main
    road within 1 km). Reads the data folder without making it."""
    if any(r.found == ROAD_FOUND for r in cfg.roads):
        return "ok"
    folder = None
    try:
        folder = road_data.data_dir(config_path(cfg), create=False)
        if (folder / road_data.PENDING_NAME).exists():
            return "failed"
    except OSError:
        pass
    if cfg.roads:
        return "manual"
    outcome = road_data.read_outcome(folder) if folder is not None else None   # what setup recorded; none: it hasn't looked
    return outcome if outcome in ("none", "not_built") else "none"


def use_config(cfg):
    """Point the server at a config (config.py): the home, its clock, the road and the limits. Runs once at import;
    tests run it again with another config. With None (no config) there is no home: Task 10 decides what happens then.
    The config rounds the home to ~1 km on purpose (public repo): weather, pollen, smoke and the 15 km radius don't
    need more. tzdata (requirements.txt) gives every host the timezone database."""
    global CFG, APP_VIEW, HOME_LAT, HOME_LON, HOME_TZ, ROAD, RUSH_WINDOWS, LIMITS, PM_FINE, PM_HIGH, CO2_OPEN, RH_LOW, RH_DAMP
    CFG = cfg
    APP_VIEW = public_view(cfg) if cfg else {}   # what the page gets as window.APP
    if cfg:
        APP_VIEW["roads_status"] = roads_status(cfg)
    place = cfg.place if cfg else None
    HOME_LAT, HOME_LON = (place.lat, place.lon) if place else (None, None)
    HOME_TZ = ZoneInfo(place.timezone) if place else None
    ROAD = cfg.road if cfg else None
    RUSH_WINDOWS = list(ROAD.rush) if ROAD else []   # [start, end) hours, and the rush's name
    # the TOML reads every limit as a float; whole numbers stay whole so the page says "800", not "800.0"
    LIMITS = {k: int(v) if float(v).is_integer() else v for k, v in (cfg.limits if cfg else DEFAULT_LIMITS).items()}
    PM_FINE, PM_HIGH, CO2_OPEN = LIMITS["pm"], LIMITS["pm_high"], LIMITS["co2"]
    RH_LOW, RH_DAMP = LIMITS["rh_dry"], LIMITS["rh_damp"]
    summary_rules.LIMITS.update(LIMITS)
    summary_rules.APPLIANCES.clear()
    summary_rules.APPLIANCES.update(cfg.appliances if cfg else APPLIANCES)
    phrases.LIMIT_FACTS.update(phrases.PLACE_DEFAULTS)   # a config without a monitor doesn't keep the last one's name
    phrases.LIMIT_FACTS.update(co2_limit=LIMITS["co2"], rh_damp=LIMITS["rh_damp"], rh_dry=LIMITS["rh_dry"],
                               rh_stop=LIMITS["rh_stop"], pm_limit=LIMITS["pm"], pm_high=LIMITS["pm_high"],
                               **({k: v for k, v in (("indoor", place.indoor), ("outdoor", place.outdoor)) if v} if place else {}))


def startup_config(env=os.environ, root=None):
    """The config to run with: DEMO=1, CONFIG_TOML, CONFIG_FILE or config.toml (config.load_config). With none of them and no
    AIRGRADIENT_TOKEN either (a fresh copy), the demo's."""
    kw = {} if root is None else {"root": root}
    cfg = load_config(env, **kw)
    if cfg is None and not real_value(env.get("AIRGRADIENT_TOKEN")):
        cfg = load_config({"DEMO": "1"}, **kw)
    return cfg


def _stop(message):
    print(message, file=sys.stderr, flush=True)
    sys.exit(APP_LOAD_ERROR)


def boot_config(env=os.environ, root=None):
    """startup_config for the server's start: stops with a plain message instead of a traceback when the config is
    invalid (the ConfigError's text names where) or when there is a token but no config (it won't guess a home)."""
    try:
        cfg = startup_config(env, root)
    except ConfigError as e:
        _stop(str(e))
    if cfg is None:
        _stop(NO_CONFIG_MESSAGE)
    return cfg


def server_mode(cfg, token):
    """"demo" (made-up data, demo/), "live" (a config and a token), "local" (no token, a monitor with a serial: read on the
    Wi-Fi) or "no_token" (a config, no token and no monitor)."""
    if cfg is not None and cfg.demo:
        return "demo"
    if real_value(token):
        return "live"
    return "local" if cfg is not None and cfg.place is not None and config_monitors(cfg) else "no_token"


use_config(boot_config())

OFFLINE_AFTER_MIN = int(OFFLINE_AFTER.total_seconds() // 60)   # the page's Online / Late / Offline dots use the alerts' Offline point


MODE = server_mode(CFG, TOKEN)   # "demo", "live", "local" or "no_token"; local token entry switches to live

STORE = None    # the home's own history (history_store); None where this copy doesn't read monitors on the Wi-Fi
POLLER = None   # reads the monitors on the Wi-Fi every minute; None where there is nothing to read that way


def start_local_reads():
    """The history store and the poller for the config's monitors with a serial. Only a config that holds serials:
    the tests' reference config has none, so they never browse."""
    global STORE, POLLER
    import address_memory
    import history_store
    import localnet
    STORE = STORE or history_store.HistoryStore(history_store.store_path())
    POLLER = localnet.Poller(config_monitors(CFG), browse=localnet.environment_blocker() is None,
                             on_row=lambda row: STORE.add(row["serialno"], row),
                             on_resume=lambda mon, gap_s: sources.start_fill("resume"),
                             memory=address_memory.AddressMemory(address_memory.default_path()))   # last good address first after a restart
    POLLER.start()


if sources.local_reads_on():
    start_local_reads()
    sources.start_fill("start")


def get_build_info():
    """The commit and the time of this page, in the home's timezone. The commit comes from the host's deploy
    variables when it sets them (Railway, Vercel), or from the Docker image's (GIT_COMMIT_SHA, the GIT_SHA build arg),
    else from git."""
    commit = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("VERCEL_GIT_COMMIT_SHA")
              or os.environ.get("GIT_COMMIT_SHA") or "").strip()[:7]
    if not commit:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).parent, stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
        except Exception:
            commit = "no-git"
    return f"{APP_VERSION} · {commit} · {sources.local_now().strftime('%H:%M:%S %Z')}"


_ASSET_HASHES = {}   # path -> ((mtime, size), hash): each file is hashed once, again only when it changes on disk


def asset_version(rel_path):
    """Short content hash of a file in static/, so a changed file gets a new URL and no cache serves the old one.
    Hashed once per file and kept; a file edited while the server runs (local work) gets a new hash on its next use."""
    path = Path(__file__).parent / rel_path.lstrip("/")
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        known = _ASSET_HASHES.get(path)
        if known and known[0] == stamp:
            return known[1]
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"
    _ASSET_HASHES[path] = (stamp, digest)
    return digest


def version_assets(html):
    """Add ?v=<hash> to the page's CSS, JS, icons and manifest, so a changed file gets a new URL. Safari keeps
    home-screen icons per URL, so a new icon needs a new URL to show."""
    return re.sub(r'"(/static/[\w./-]+\.(?:css|js|png|svg)|/manifest\.json)"', lambda m: f'"{m.group(1)}?v={asset_version(m.group(1))}"', html)


# Names a copy on a home network answers besides LOCAL_HOST's: the ones home routers hand out (.lan, .home, .home.arpa,
# .internal, .localdomain, a FRITZ!Box's .fritz.box). None of them can be registered by an outside site.
HOME_NETWORK_SUFFIXES = (".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".fritz.box")
# Hosting markers that also appear at home: a NAS's apps (TrueNAS SCALE) run on Kubernetes, so that marker alone
# doesn't make a copy public.
HOME_TOO_MARKERS = ("KUBERNETES_SERVICE_HOST",)
FOREIGN_HOST_MESSAGE = ("Home Air answers only its own addresses on this network. To reach it at {host}, add that name "
                        "to HOME_AIR_HOSTS (a comma-separated list) in .env, then start Home Air again.\n")


def _host_name():
    """The Host header without its port or a final dot, lowercased: "[::1]:5555" -> "::1", "Mac.local.:5555" ->
    "mac.local". Read from the raw header: Werkzeug's request.host drops an IPv6 address with a zone ("[fe80::1%25en0]")."""
    host = (request.environ.get("HTTP_HOST") or request.host or "").strip().lower()
    if host.startswith("["):
        return unquote(host[1:].split("]", 1)[0])
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host[:-1] if host.endswith(".") else host


def _own_host(name):
    """A name a web page on another site can't make the browser send. Such a page can point its own domain at this
    computer (DNS rebinding) and read the answers, since the browser then treats them as that page's own. An address
    typed as digits, a bare machine name, Bonjour's .local, Tailscale's ts.net and the home routers' names never
    belong to an outside site; any other name must be listed in HOME_AIR_HOSTS."""
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if LOCAL_HOST.match(name) or name.endswith(HOME_NETWORK_SUFFIXES):
        return True
    extra = {h.strip().lower() for h in os.environ.get("HOME_AIR_HOSTS", "").split(",") if h.strip()}
    return name in extra


@app.before_request
def refuse_foreign_host():
    """A copy on a home network refuses a name from another site (421, "sent to the wrong server"). A hosted copy
    (Railway, Vercel) is on the internet already: its public name is the way in, so it answers any name."""
    if token_setup.hosted({k: v for k, v in os.environ.items() if k not in HOME_TOO_MARKERS}):
        return None
    name = _host_name()
    if _own_host(name):
        return None
    return Response(FOREIGN_HOST_MESSAGE.format(host=name), status=421, mimetype="text/plain")


# Responses a browser gets gzipped when it asks: the page, CSS, JS, JSON, SVG and the manifest. Images are compressed already.
COMPRESSIBLE = re.compile(r"^(text/|application/(javascript|json|manifest\+json|geo\+json|xml)|image/svg\+xml)")
COMPRESS_MIN_BYTES = 1024    # smaller bodies gain nothing worth the work
COMPRESS_MAX_BYTES = 4 << 20   # a file sent from disk is read whole to compress it; the app's own are far smaller
_GZIPPED = {}   # (path, ETag) -> gzipped body, for the files sent from disk (static/), which only change with a new ETag
IMMUTABLE = "public, max-age=31536000, immutable"


@app.after_request
def static_cache(resp):
    """A /static/ URL with the file's current ?v=<hash> (version_assets) never changes, so a browser keeps it for a year
    without asking. Any other /static/ request (no ?v=, or an old hash) keeps Flask's no-cache. Vercel's CDN serves
    /static/ itself there (scripts/vercel_build.sh), so this is for the copies a home runs (gunicorn, Docker)."""
    if (request.path.startswith("/static/") and resp.status_code in (200, 304)
            and request.args.get("v") and request.args["v"] == asset_version(request.path)):
        resp.headers["Cache-Control"] = IMMUTABLE
    return resp


@app.after_request
def compress(resp):
    """Gzip text answers for a browser that accepts it (the copies a home runs: gunicorn sends what Flask gives it).
    Vercel compresses at its edge, so the demo there skips this. Brotli would need a dependency; gzip is in the standard
    library and gets app.js from ~114 KB to ~30 KB."""
    if (os.environ.get("VERCEL") == "1" or request.method == "HEAD" or resp.status_code != 200
            or "Content-Encoding" in resp.headers or not COMPRESSIBLE.match(resp.mimetype or "")):
        return resp
    if resp.is_streamed and not resp.direct_passthrough:
        return resp   # a generator's answer: compressing it would wait for all of it
    size = resp.content_length
    if size is not None and not (COMPRESS_MIN_BYTES <= size <= COMPRESS_MAX_BYTES):
        return resp
    resp.vary.add("Accept-Encoding")   # a cache in between must not hand the gzipped copy to a client that didn't ask
    if request.accept_encodings["gzip"] <= 0:
        return resp
    etag = resp.headers.get("ETag")
    key = (request.path, etag) if resp.direct_passthrough and etag else None
    body = _GZIPPED.get(key) if key else None
    if body is None:
        source = resp.response
        resp.direct_passthrough = False   # send_file's file is read here, once, then kept gzipped
        data = resp.get_data()
        if hasattr(source, "close"):
            source.close()
        if len(data) < COMPRESS_MIN_BYTES:
            return resp
        body = gzip.compress(data, compresslevel=6, mtime=0)
        if key:
            if len(_GZIPPED) > 64:
                _GZIPPED.clear()
            _GZIPPED[key] = body
    elif hasattr(resp.response, "close"):
        resp.response.close()   # the kept copy answers; the opened file is not read
    resp.direct_passthrough = False
    resp.set_data(body)
    resp.headers["Content-Encoding"] = "gzip"
    if etag and not etag.startswith("W/"):
        # the gzipped bytes differ from the file's, so the tag becomes weak; If-None-Match still matches it (304)
        resp.headers["ETag"] = "W/" + etag
    return resp


@app.after_request
def api_headers(resp):
    """No browser HTTP caching of live data (the server cache does that); say how fresh the answer is."""
    if request.path.startswith("/api/"):
        # never kept: the token form, and the setup state the Connected card polls while setup restarts the app
        resp.headers["Cache-Control"] = "no-store" if request.path in ("/api/token", "/api/setup") or request.path.startswith(("/api/agent/", "/api/alerts")) else "no-cache"
        if getattr(g, "cache_state", None):
            resp.headers["X-Cache"] = g.cache_state
            resp.headers["X-Data-Age"] = str(g.cache_age)
    if (MODE == "demo" and request.method == "GET" and resp.status_code == 200 and (request.path == "/" or request.path.startswith("/api/"))
            and resp.headers.get("Cache-Control") != "no-store"):
        # a page that must not be kept (a fresh copy's setup card) stays so: a kept card would come back after setup
        if request_monitor() is not None or request.path == "/api/monitors":
            # a public monitor is live: a CDN keeps it as long as its data stays fresh in the cache (demo/public.py)
            resp.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"
        else:
            # the demo's story is fixed: a CDN keeps each URL (each ?moment=) for a day, so a busy day barely runs the server
            resp.headers["Cache-Control"] = "public, max-age=300, s-maxage=86400"
    if MODE == "demo" and monitor_missing():
        # a public monitor that can't be shown (unknown id, or the map didn't answer): no one keeps this answer
        resp.headers["Cache-Control"] = "no-store"
    # only the demo is for search engines; someone's home is not, nor a public monitor's page or the picker's list
    # (they carry the owners' names)
    if not (CFG is not None and CFG.demo) or "monitor" in request.args or request.path == "/api/monitors":
        resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def icon_env():
    """"dev" or "preview" or None (a normal deployment). The reloading dev server (`flask run --reload`) at a local
    address is "dev"; otherwise a deployment names itself with APP_ENV, never by its address: a home that runs the app
    on its own network (a laptop, a small home server at 192.168.…) is a normal deployment."""
    host = (request.host or "").split(":")[0].lower()
    if LOCAL_HOST.match(host) and sources._dev_server():
        return "dev"
    env = os.environ.get("APP_ENV", "").strip().lower()
    return env if env in ENV_LABEL else None


def tag_env(html):
    """DEV / PREVIEW icons, tab title and home-screen name, so the environments can't be mixed up."""
    env = icon_env()
    if not env:
        return html
    html = re.sub(r'(<link rel="(?:icon|apple-touch-icon)"[^>]*href=")/static/icons/', rf'\1/static/icons/{env}/', html)
    html = html.replace("<title>Home Air</title>", f"<title>{ENV_LABEL[env]} · Home Air</title>", 1)
    html = html.replace('<meta name="apple-mobile-web-app-title" content="Home Air">', f'<meta name="apple-mobile-web-app-title" content="HA {ENV_LABEL[env]}">', 1)
    return html


def inline_touch_icon(html):
    """Put the 180 px home-screen icon in the page itself. Safari's Add to Home Screen sheet loads icons on its own
    and can fail on a network URL, which leaves a letter tile."""
    m = re.search(r'(<link rel="apple-touch-icon"[^>]*href=")(/static/icons/[\w./-]+\.png)(?:\?v=\w+)?"', html)
    if not m:
        return html
    try:
        data = base64.b64encode((Path(__file__).parent / m.group(2).lstrip("/")).read_bytes()).decode()
    except OSError:
        return html
    return html[:m.start()] + f'{m.group(1)}data:image/png;base64,{data}"' + html[m.end():]


@app.route("/")
def index():
    return render_page()


@app.route("/connect")
def connect():
    """The token box on this computer's page, whenever no token is saved (also when monitors are read on the Wi-Fi).
    Anywhere else, or with a token saved, the card says where to add it instead. An old private link (?code=) lands here."""
    if token_setup.forwarded(request):
        return Response(FORWARDED_ERROR + "\n", 403, mimetype="text/plain", headers={"Cache-Control": "no-store"})
    if request.args.get("saved") == "1" and token_state.saved and token_setup.has_token(env_path, TOKEN):
        return render_page(card=True)   # right after a save: "Saved. Go back to your agent's chat and reply done."
    if not token_mode() or token_setup.has_token(env_path, TOKEN):
        return redirect("/", code=303)
    if "code" in request.args:
        return redirect("/connect", code=303)
    return render_page(card=True)


def sets_cookie(response, name):
    return any(h.startswith(name + "=") for h in response.headers.getlist("Set-Cookie"))


@app.after_request
def tidy_token_cookies(response):
    """Another program on localhost can plant cookies of the same names (ports share cookies; a more specific path is
    sent first). Each value is checked; a value this server didn't issue is cleared at both paths, keeping a valid one."""
    if (request.path not in ("/connect", "/api/token") and not (request.path == "/" and token_mode())
            or not token_setup.local_entry(request, env_path)):
        return response
    checks = []
    forms = request.cookies.getlist(token_setup.COOKIE_NAME)
    if forms:
        known = [token_state.known(v) for v in forms]
        checks.append((token_setup.COOKIE_NAME, any(known), not all(known)))
    for name, valid, stray in checks:
        if not stray:
            continue
        for path in token_setup.COOKIE_PATHS:
            if path != "/" or not (valid or sets_cookie(response, name)):
                response.delete_cookie(name, path=path)
    return response


def setup_mode():
    # A fresh copy opens the demo; the token box at /connect collects the token before setup can discover monitors.
    return MODE == "no_token" or (MODE == "demo" and CFG.demo and os.environ.get("DEMO") != "1")


def token_mode():
    """The modes in which the token box may open: no token saved, whether nothing is read yet or monitors are read on the Wi-Fi."""
    return setup_mode() or MODE == "local"


def print_setup_link(port):
    """The start line names where the token goes, when none is saved."""
    if not real_value(TOKEN):
        print(f"No AirGradient API token yet. To add it: http://localhost:{port}/connect", flush=True)


TOKEN_FORMAT_ERROR = ("This doesn't look like an AirGradient token. Press the copy button next to API Access "
                      "(not \"Renew API token\"), then paste it here.")
TOKEN_REFUSED_ERROR = ("AirGradient refused this token. Check that API Access is turned on for the place that shows your monitor, "
                       "then copy the token again.")
FORM_EXPIRED_ERROR = "This form had expired, so nothing was sent. Paste the token again."
CONNECT_CMD = "./homeair connect"   # opens the token box's address (scripts/setup.py --connect)
FORWARDED_ERROR = "The token box opens only on the computer Home Air runs on, not through a proxy or tunnel."
# The public demo's head only (never a copy a household runs): Vercel Web Analytics and Speed Insights (cookieless, nothing
# personal; the app itself has no analytics) and the link preview shown when someone shares the demo's address. Vercel serves
# the two scripts itself once the project has them on.
DEMO_SITE = "https://homeair.app"
DEMO_TITLE = "Home Air"
DEMO_DESCRIPTION = ("See your air quality data next to the weather, pollen, smoke and traffic, "
                    "with insights for your home and outside.")
DEMO_HEAD_TAGS = (
    '<script>window.va = window.va || function () { (window.vaq = window.vaq || []).push(arguments); };</script>'
    '<script defer src="/_vercel/insights/script.js"></script>'
    '<script>window.si = window.si || function () { (window.siq = window.siq || []).push(arguments); };</script>'
    '<script defer src="/_vercel/speed-insights/script.js"></script>'
    f'<meta name="description" content="{DEMO_DESCRIPTION}">'
    f'<meta property="og:type" content="website"><meta property="og:site_name" content="{DEMO_TITLE}">'
    f'<meta property="og:title" content="Open-source app for your AirGradient monitors">'
    f'<meta property="og:description" content="{DEMO_DESCRIPTION}"><meta property="og:url" content="{DEMO_SITE}/">'
    f'<meta property="og:image" content="{DEMO_SITE}/static/og-demo.png">'
    '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta property="og:image:type" content="image/png">'
    f'<meta property="og:image:alt" content="Home Air on a phone, with an action for each person at home">'
    '<meta name="twitter:card" content="summary_large_image">')


def demo_head_tags():
    return DEMO_HEAD_TAGS if os.environ.get("VERCEL") == "1" and os.environ.get("DEMO") == "1" else ""


def render_page(error=None, status=200, card=False):
    """The missing-token card works without JavaScript; no credential is ever embedded in the page.
    card: show the token card even where the page would show the demo or the home (/connect, an expired form)."""
    html_path = Path(__file__).parent / "index.html"
    html = inline_touch_icon(tag_env(version_assets(html_path.read_text())))
    html = html.replace("</head>", demo_head_tags() + "</head>", 1)
    browser = None
    # A fresh copy (the demo, no home yet) shows only the card: the token box with its link, then "saved" until setup restarts it.
    just_saved = card and request.args.get("saved") == "1" and bool(token_state.saved)   # /connect?saved=1 after a save
    saved = (setup_mode() and MODE == "demo" and token_state.saved) or just_saved
    show_setup = MODE == "no_token" or saved or (token_mode() and card)
    html = inject_app(html, setup_page=show_setup)
    if show_setup:
        entry = token_setup.allowed(request, env_path, TOKEN, "no_token" if MODE != "local" else "local")
        open_box = entry and not saved   # this computer's page: the checks in token_setup.allowed
        value, browser = token_state.issue(request) if open_box and token_setup.form_request(request) else (None, None)
        card = render_template("token_card.html", entry=bool(open_box and value), saved=saved, home_ready=just_saved,
                               forms_full=bool(open_box and not value), connect_cmd=CONNECT_CMD,
                               found=token_state.saved["monitors"] if saved else [], shown=SAVED_NAMES_SHOWN, nonce=value, error=error,
                               instructions=token_setup.instructions(request), port=token_setup.app_port(), max_length=token_setup.TOKEN_MAX_LENGTH)
        card = version_assets(card)
        html = html.replace('id="no-token" hidden', 'id="no-token"')
        html = html.replace("<!-- token-setup -->", card)

    # Inject build info into a data attribute on the body
    build_info = get_build_info()
    html = html.replace(
        '<body>',
        f'<body data-build="{build_info}">'
    )

    if show_setup:
        # fresh-copy: no home yet, so the header has no home menu to show
        html = html.replace('<body data-build=', '<body class="no-token%s" data-build=' % (" fresh-copy" if MODE == "demo" else ""), 1)
    response = make_response(html, status)
    response.headers["Referrer-Policy"] = "same-origin"
    if show_setup:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        response.headers["X-Frame-Options"] = "DENY"
    if browser:
        response.set_cookie(token_setup.COOKIE_NAME, browser, httponly=True, samesite="Strict", secure=request.is_secure)
    return response


@app.route("/api/token", methods=["POST"])
def save_token():
    """The token box's save, whenever no token is saved. Validate unlocked, then serialize the final check and save.
    Every answer that keeps the box open shows it with its error (card=True): in "local" mode / is the home, not the box."""
    global TOKEN, MODE
    with token_state.lock:
        if (MODE == "demo" and not setup_mode()) or not token_setup.local_entry(request, env_path) or not token_setup.same_origin(request):
            return jsonify(error="Token entry is only available on this computer's local page."), 403
        if token_setup.has_token(env_path, TOKEN):
            return redirect("/", code=303)   # sent twice, or from an old tab: the page shows where things stand
        if not token_mode():
            return jsonify(error="Token entry is only available on this computer's local page."), 403
        request.max_content_length = token_setup.TOKEN_POST_MAX_BYTES
        if not token_state.consume(request, request.form.get("setup_nonce", "")):
            # A page, never raw JSON: a fresh form, ready for the token.
            return render_page(FORM_EXPIRED_ERROR, 403, card=True)
        retry = token_state.retry_after()
        if retry:
            response = render_page("Too many attempts. Wait five minutes, then try again.", 429, card=True)
            response.headers["Retry-After"] = str(retry)
            return response
        candidate = request.form.get("token", "").strip()
        if (not candidate or len(candidate) > token_setup.TOKEN_MAX_LENGTH
                or not token_setup.TOKEN_CHARACTERS.fullmatch(candidate) or not real_value(candidate)):
            token_state.failed()
            return render_page(TOKEN_FORMAT_ERROR, 400, card=True)
    try:
        accepted = sources.validate_token(candidate)
    except (sources.requests.RequestException, ValueError):
        with token_state.lock:
            token_state.failed()
            return render_page("AirGradient didn't answer. Try again shortly.", 502, card=True)

    with token_state.lock:
        if accepted is None:
            token_state.failed()
            return render_page(TOKEN_REFUSED_ERROR, 400, card=True)
        # Recheck for a token written while the validation call was waiting.
        if token_setup.has_token(env_path, TOKEN):
            return redirect("/", code=303)   # another tab or the terminal won: keep theirs, show the page
        try:
            write_env_key(env_path, "AIRGRADIENT_TOKEN", candidate)
        except (OSError, UnicodeError, ValueError):
            return render_page("Couldn't save the token. Check that the app's folder is writable.", 500, card=True)
        os.environ["AIRGRADIENT_TOKEN"] = candidate
        home_ready = MODE == "local"   # monitors already read on the Wi-Fi: the home is there; the card says to go back to the agent
        TOKEN, MODE = candidate, server_mode(CFG, candidate)
        budgets.clear("ag_current")   # a refused token's pause ends with a new one
        budgets.clear("ag_history")
        take_account(accepted)
        start_alerts()   # Home Air with nothing to read before the token reads the home now
        token_state.saved = {"monitors": sources.account_monitors(accepted)}   # their names and kinds, for the "connected" card
        token_state.nonces.clear()
        token_state.failures.clear()
        response = redirect("/connect?saved=1" if home_ready else "/", code=303)
        response.headers["Cache-Control"] = "no-store"
        response.delete_cookie(token_setup.COOKIE_NAME)
        return response


ROAD_SWITCH_REFUSED = ("Road switches work on the computer Home Air runs on. "
                       "Ask your agent: \"Turn off the BQE in Home Air\".")
ROAD_SWITCH_STALE = "This road is no longer in your config. Reload the page."
ROAD_SWITCH_FULL = f"Home Air follows at most {roads_mod.ROADS_MAX} roads; turn another road off first."


@app.route("/api/roads/switch", methods=["POST"])
def switch_road():
    """A road's switch on the page: set `on` of one [[roads]] entry in config.toml, a backup first, then use the new config
    (APP.roads shows it at once). Only on the computer Home Air runs on, with the token form's guards; online it is read-only.
    The file is read again under the lock, so a switch from a stale page (no such key now) answers 409 and writes nothing."""
    path = config_path(CFG)
    if (path is None or not token_setup.local_entry(request, path.parent) or not token_setup.same_origin(request)
            or not request.is_json):
        return jsonify({"error": ROAD_SWITCH_REFUSED}), 403
    data = request.get_json(silent=True)
    key, on = (data.get("key"), data.get("on")) if isinstance(data, dict) else (None, None)
    if not isinstance(key, str) or not key or not isinstance(on, bool):
        return jsonify({"error": 'POST {"key": "<road key>", "on": true|false}'}), 400
    with token_state.lock:
        try:
            text = path.read_text(encoding="utf-8")
            new = set_road_on(text, key, on)
            cfg = load_config({"CONFIG_TOML": new})   # never write a file the app can't start with
        except KeyError:
            return jsonify({"error": ROAD_SWITCH_STALE}), 409
        except (OSError, UnicodeError, ConfigError):
            return jsonify({"error": "Couldn't change config.toml. Run ./homeair check --config to see the problem."}), 500
        if on and sum(r.on for r in cfg.roads) > roads_mod.ROADS_MAX:
            return jsonify({"error": ROAD_SWITCH_FULL}), 409
        try:
            if new != text:
                backup_config(path)
                replace_text(path, new)
            cfg = load_config(os.environ)   # from the file (the check's config came from text: its source isn't the file's)
        except (OSError, UnicodeError, ConfigError):
            return jsonify({"error": "Couldn't change config.toml. Check that the app's folder is writable."}), 500
        use_config(cfg)
    return jsonify({"roads": APP_VIEW["roads"], "roads_status": APP_VIEW["roads_status"]})


def take_account(rows):
    """After a token is saved: the home's monitors matched to the account's (sources.match_account) are written to the
    config file, a backup first, and the app switches to it with no restart: the new mode, the poller's monitors, then
    the history's last FILL_DAYS filled from the cloud. An indoor-only home with a place gets the area's estimate as its
    outdoor source (the config needs one). A config that isn't a file (CONFIG_TOML), the demo, or a file that can't be
    read or written stays as it was; the token is saved either way."""
    global MODE
    path = config_path(CFG)
    if path is None:
        return
    try:
        text = path.read_text(encoding="utf-8")
        new = set_monitor_keys(text, sources.match_account(load_config({"CONFIG_TOML": text}), rows))
        try:
            load_config({"CONFIG_TOML": new})
        except ConfigError:   # an indoor monitor with a place and no outdoor one: the area's estimate, which needs no call now
            new = add_key(new, "sources", "outdoor_from", "area")
            load_config({"CONFIG_TOML": new})   # never write a file the app can't start with
        if new != text:
            backup_config(path)
            replace_text(path, new)
        cfg = load_config(os.environ)
    except (OSError, UnicodeError, ConfigError) as error:
        print(f"The token is saved, but the monitors couldn't be written to the config ({type(error).__name__})", file=sys.stderr, flush=True)
        return
    if cfg is None or cfg.demo:
        return
    use_config(cfg)
    MODE = server_mode(CFG, TOKEN)
    if POLLER is not None:
        POLLER.use(config_monitors(CFG))
    elif sources.local_reads_on():
        start_local_reads()
    sources.start_fill("token")


def adopt_saved_token(start=lambda fn: threading.Thread(target=fn, daemon=True, name="token-match").start()):
    """A token typed into .env by hand never went through the box: at start, when a monitor read on the Wi-Fi has no
    cloud id yet, do what saving it on the page does (take_account: the ids written with a backup, the 7-day fill),
    after one call for the account's monitors. In the background: the start never waits on the cloud."""
    if MODE != "live" or POLLER is None or not any(m.location_id in sources.localnet.LOCAL_IDS.values() for m in config_monitors(CFG)):
        return

    def match():
        try:
            rows = sources.validate_token(TOKEN)
        except (sources.requests.RequestException, ValueError) as error:
            print(f"AirGradient didn't answer, so the monitors' cloud ids weren't matched ({type(error).__name__}); "
                  "the next start tries again", file=sys.stderr, flush=True)
            return
        if rows is None:
            print("AirGradient refused the token in .env: the monitors stay read on the Wi-Fi", file=sys.stderr, flush=True)
            return
        with token_state.lock:
            take_account(rows)
    start(match)


adopt_saved_token()


def page_view():
    """window.APP's config part: the home's (config.public_view). demo/data.py gives a public monitor's instead."""
    return APP_VIEW


def page_clock():
    """The demo's fixed clock for the page (window.APP.now: the moment's time, its key, the moments to switch to),
    or None: a real home runs on the real clock. demo/data.py replaces it."""
    return None


def inject_app(html, setup_page=False):
    """The home's config for the page's scripts (config.public_view), as window.APP before the first of them.
    Every "<" is escaped (\\u003c, still the same JSON) so no config value can close the script or open a comment."""
    now = None if setup_page else page_clock()   # the token card never shows the demo's moments
    import alerts   # here: server.py imports it at the end, after the routes
    alerts_state = alerts.available(request, demo=MODE == "demo", reading=reads_home())   # where this request's page can turn alerts on
    data = json.dumps({**page_view(), "offline_after_min": OFFLINE_AFTER_MIN, "alerts": alerts_state, "alerts_quiet": list(QUIET_DEFAULT),
                       "env": icon_env(), **({"now": now} if now else {}), **({"state": "no_token"} if MODE == "no_token" or setup_page else {"state": "local"} if MODE == "local" else {})}, ensure_ascii=False).replace("<", "\\u003c")
    return html.replace("<script src=", f"<script>window.APP = {data};</script>\n<script src=", 1)


@app.route("/api/setup")
def setup_state():
    """Whether the app still waits for setup. The Connected card asks every few seconds and opens the home once setup has
    restarted the app with it, so nobody has to reload the tab."""
    return jsonify({"setup": setup_mode()})


ROAD_INDEX_MAX_DIGITS = 3   # digits of ?i= read as an index into the config's roads (a home has a handful)


def hand_route(route):
    try:
        return jsonify(json.loads(route_path(route).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return jsonify({"error": "road route unreadable"}), 404


@app.route("/api/road/route")
def road_route():
    """The hand-set road's route ([lon, lat] points) for the map; 404 without one. With ?i=<index in the config's
    roads>: a road found at setup gets its lines ([[lon, lat], ...] each) within road_data.ROUTE_CLIP_M of the rounded
    place, from the cached road file; 404 when the file is missing or can't be read."""
    if "i" not in request.args:
        return hand_route(ROAD.route) if ROAD else (jsonify({"error": "no road"}), 404)
    i, roads = request.args["i"], CFG.roads if CFG else ()
    if not (i.isascii() and i.isdigit() and len(i) <= ROAD_INDEX_MAX_DIGITS and int(i) < len(roads)):
        return jsonify({"error": "no such road"}), 404
    road = roads[int(i)]
    if road.route:   # the hand-set [road]
        return hand_route(road.route)
    lines = None
    if road.found == ROAD_FOUND and HOME_LAT is not None and road.data and road.key:
        if CFG.demo:
            try:
                from demo.roads import route_stretch
                lines = route_stretch(road.data, road.key, HOME_LAT, HOME_LON)
            except (OSError, RuntimeError, ValueError, KeyError):
                pass   # a missing or damaged bundle is not replaced through the network
        else:
            lines = road_data.route_stretch(road_data.data_dir(config_path(CFG), create=False), road.data, road.key,
                                            HOME_LAT, HOME_LON)
    if lines is None:
        return jsonify({"error": "road route unavailable" if CFG.demo else "road data not downloaded"}), 404
    return jsonify(lines)


def start_road_data(start=lambda fn: threading.Thread(target=fn, daemon=True, name="road-data").start()):
    """At start, in the background: on this computer, when the road data couldn't be fetched at setup and a try is due
    (road_data.retry_due: at most once a day), setup's road step again and the new config; on every copy, one GET for
    each dated metro or block file named in the config that isn't cached (a restart on Railway or a removed file).
    Failed setup uses the same core selection on retry; a saved road keeps its dated file, never today's replacement.
    The start never waits on the data host."""
    if MODE == "demo" or CFG is None:
        return
    path = config_path(CFG)

    def run():
        try:
            if path is not None and token_setup.local_machine():
                # the downloads run outside the lock; only the write of config.toml (and the switch to it) takes it
                res = road_data.retry_at_start(path, datetime.now(timezone.utc), lock=token_state.lock)
                if res is not None:
                    print(" ".join(res.lines) or f"Roads: {res.status}", file=sys.stderr, flush=True)
                    if res.status != "failed":
                        with token_state.lock:
                            cfg = load_config(os.environ)
                            if cfg is not None and not cfg.demo:
                                use_config(cfg)
            failed = road_data.cache_files(road_data.data_dir(path, create=False), CFG.roads)
            if failed:
                print(f"Roads: couldn't get {', '.join(failed)}; the roads are named but not drawn until the next start",
                      file=sys.stderr, flush=True)
        except (OSError, ConfigError) as error:
            print(f"Roads: the road data step stopped ({type(error).__name__})", file=sys.stderr, flush=True)
    start(run)


start_road_data()


@app.route("/manifest.json")
def manifest():
    path = Path(__file__).parent / "manifest.json"
    env = icon_env()
    data = json.loads(path.read_text())
    if env:   # the installed app gets the DEV / PREVIEW icons and name too
        data["name"] = f'{data.get("name", "Home Air")} ({ENV_LABEL[env]})'
        data["short_name"] = f"HA {ENV_LABEL[env]}"
    for ic in data.get("icons", []):
        src = str(ic.get("src", ""))
        if src.startswith("/static/icons/"):
            ic["src"] = (src.replace("/static/icons/", f"/static/icons/{env}/", 1) if env else src) + f"?v={asset_version(src)}"
    resp = jsonify(data)
    resp.headers["Content-Type"] = "application/manifest+json"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
@app.route("/apple-touch-icon-180x180.png")
@app.route("/apple-touch-icon-180x180-precomposed.png")
@app.route("/favicon.ico")
def root_icon():
    """The icons iOS and browsers ask for at the root, whatever the page declares; with the DEV / PREVIEW set where it applies."""
    env = icon_env()
    name = "favicon-32.png" if request.path == "/favicon.ico" else "apple-touch-icon.png"
    path = Path(__file__).parent / "static" / "icons" / (f"{env}/" if env else "") / name
    resp = send_file(path, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/sw.js")
def service_worker():
    return send_file(Path(__file__).parent / "sw.js", mimetype="application/javascript")


@app.route("/health")
def health():
    states = sources.monitor_states()   # kinds only: no address, no serial
    others = POLLER.others() if POLLER is not None and states else {}   # by kind: the serial's last 4 and the model
    return jsonify({
        "status": "ok",
        "airgradient_token_set": bool(TOKEN),
        **({"monitors": states} if states else {}),
        **({"also_found": others} if others else {}),
    })


@app.route("/api/usage")
def usage():
    """Today's calls to each outside API against its daily budget (budgets.py), and how often AirGradient is asked.
    Counts only: no readings, no keys."""
    return jsonify({**budgets.usage(), "refresh_seconds": sources.REFRESH_SECONDS})


@app.route("/api/current")
def current():
    if MODE == "no_token" or (MODE == "local" and POLLER is None):   # nothing to read yet: the page says so; an expired token is a 502 below
        return jsonify({"state": "no_token"})
    try:
        value, info = sources.home_rows(force=sources._force())
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    _note_cache(info)
    return jsonify(value)


@app.route("/api/history/<int:location_id>")
def history(location_id):
    chart_keep = sources.HIST_KEEP   # PM2.5, CO2 and humidity: the three History charts
    frm_to = None
    try:
        frm, to = sources._iso(request.args["from"]), sources._iso(request.args["to"])
        frm_to = (frm if frm.tzinfo else frm.replace(tzinfo=timezone.utc), to if to.tzinfo else to.replace(tzinfo=timezone.utc))
        hours = round((to - frm).total_seconds() / 3600)
        standard = hours in sources.HIST_TTL and abs((datetime.now(timezone.utc) - to).total_seconds()) < 120
    except (KeyError, ValueError):
        standard = False
    if frm_to is not None:
        kept = sources.store_rows(location_id, *frm_to)
        if kept is not None:   # the home's own history: no call to the cloud
            return jsonify([{k: r.get(k) for k in chart_keep if r.get(k) is not None} for r in kept])
    of = sources.outdoor_from()
    if isinstance(of, int) and location_id == of:   # an indoor-only home's public monitor: its history from the map
        utc = lambda t: t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        try:
            frm = utc(sources._iso(request.args["from"])) if request.args.get("from") else datetime.now(timezone.utc) - timedelta(days=1)
            to = utc(sources._iso(request.args["to"])) if request.args.get("to") else datetime.now(timezone.utc)
        except ValueError:
            return jsonify({"error": "from and to must be ISO times"}), 400
        rows = sources.source_history_rows(location_id, frm, to)
        return jsonify([{k: r.get(k) for k in chart_keep if r.get(k) is not None} for r in rows])
    try:
        if standard:   # the page's ranges ending now: served from the cache, cut to the asked start
            rows, info = sources.history_rows(location_id, hours, force=sources._force())
            _note_cache(info)
            cut = frm.strftime("%Y-%m-%dT%H:%M:%S")
            rows = [r for r in rows if str(r.get("timestamp", ""))[:19] >= cut]
        else:
            params = {k: request.args[k] for k in ("from", "to") if request.args.get(k)}
            rows = sources.api_get(f"/locations/{location_id}/measures/past", params)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify([{k: r.get(k) for k in chart_keep if r.get(k) is not None} for r in rows or [] if isinstance(r, dict)])


@app.route("/api/neighborhood")
def neighborhood():
    if sources.source_state("nearby"):   # off, or the home has no place yet
        return jsonify([])
    try:
        value = sources._fetch_nearby()   # one area request on AirGradient's map, at most every 15 min per place
    except Exception as e:
        return jsonify({"error": sources._redact(f"Neighborhood data unavailable: {e}")}), 502
    return jsonify(value)


@app.route("/api/context")
def context():
    """Return external data sources (weather, pollen, the road, smoke) as structured JSON."""
    # Fetch independents in parallel; pollen has its own hard deadline inside get_pollen_data.
    weather_fut = _CONTEXT_POOL.submit(sources.get_open_meteo_data)
    smoke_fut = _CONTEXT_POOL.submit(sources.smoke_status) if not sources.source_state("smoke") else None
    pollen_fut = _CONTEXT_POOL.submit(sources.get_pollen_days) if sources.pollen_live() else None
    air_fut = _CONTEXT_POOL.submit(sources.get_air_forecast)

    try:
        weather = weather_fut.result(timeout=12)
    except Exception:
        weather = {"current": {}}
    smoke = sources.no_smoke()
    pollen_days = {"today": [], "tomorrow": []}
    if smoke_fut:
        try:
            smoke = smoke_fut.result(timeout=12)
        except Exception:
            pass
    if pollen_fut:
        try:
            pollen_days = pollen_fut.result(timeout=6)
        except Exception:
            pass
    pollen, pollen_tomorrow = pollen_days["today"], pollen_days["tomorrow"]
    try:
        air = air_fut.result(timeout=12)
    except Exception:
        air = {}

    cur = weather.get("current", {})
    wind_speed = cur.get("wind_speed_10m")
    wind_direction = cur.get("wind_direction_10m")
    visibility = cur.get("visibility")
    uv_index = cur.get("uv_index")
    dew_point = cur.get("dew_point_2m")
    temperature = cur.get("temperature_2m")
    humidity = cur.get("relative_humidity_2m")

    wind_dir = air_read.get_wind_cardinal(wind_direction)

    # Dew point sinus risk
    if dew_point is not None and dew_point < DEW_SEVERE_DRY_C:
        dew_point_risk = "Severe dryness"
    elif dew_point is not None and dew_point < DEW_DRY_C:
        dew_point_risk = "Moderate dryness"
    else:
        dew_point_risk = "Comfortable"

    # Visibility interpretation
    if visibility is not None:
        if visibility < VISIBILITY_VERY_LOW_M:
            visibility_status = "Very low — high particulate"
        elif visibility < VISIBILITY_REDUCED_M:
            visibility_status = "Reduced"
        elif visibility < VISIBILITY_MODERATE_M:
            visibility_status = "Moderate"
        else:
            visibility_status = "Clear"
    else:
        visibility_status = "Unknown"

    forecast = air_read.hourly_forecast(weather, air)

    # the road's traffic, and whether the wind blows its exhaust here
    current_hour = sources.local_now().hour
    road = air_read.road_traffic(current_hour, wind_direction)
    road_cfg = air_read.active_road()
    rows = air_read.sky_order(air_read.roads_now(sources.local_now(), weather))
    roads = [{"name": r["road"].name, "short": r["road"].name.removeprefix("the "), "side": r["road"].side,
              "reaching": r["reaching"], "strength": r["strength"], "why": r["why"],
              "until": r["until"].isoformat() if r["until"] else None} for r in rows]
    if road_cfg:   # "bqe" is the shape the page reads today; "road" carries the flag (the page moves to it later)
        extra = {"bqe": {"traffic": road["traffic"], "wind_impact": road["wind_impact"],
                         "pollution_pressure": road["pollution_pressure"], "current_hour": current_hour},
                 "road": {"name": road_cfg.name, "toward": road["toward"]}}
    else:
        extra = {}

    # per source: ok, off (config), not_set_up (key missing) or error (on, but it didn't answer)
    pollen_s = sources.pollen_state()
    status = {
        "pollen": ("ok" if pollen else "error") if pollen_s == "ok" else pollen_s,
        "smoke": sources.source_state("smoke") or ("ok" if smoke.get("checked") else "error"),
        "road": "ok" if road_cfg or any(r.on for r in air_read.home_roads()) else "off",
        "nearby": sources.source_state("nearby") or "ok",
    }
    if not sources.place_known():   # the weather and the air forecast wait for a place too (the others say so above)
        status["weather"] = status["air_forecast"] = "needs_place"

    return jsonify({
        "weather": {
            "wind_speed": wind_speed,
            "wind_direction": wind_direction,
            "wind_cardinal": wind_dir,
            "dew_point": dew_point,
            "dew_point_risk": dew_point_risk,
            "visibility": visibility,
            "visibility_status": visibility_status,
            "uv_index": uv_index,
            "temperature": temperature,
            "humidity": humidity,
            "cloud_cover": cur.get("cloud_cover"),
            "weather_code": cur.get("weather_code")
        },
        **extra,
        "roads": roads,
        "smoke": smoke,
        "pollen": pollen,
        "pollen_tomorrow": pollen_tomorrow,
        "forecast": forecast,
        "status": status,
    })


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """The page's air summary as one JSON object. Only "format": "read" is served."""
    data = request.get_json(silent=True) or {}
    if data.get("format") == "read":
        return jsonify(air_read.build_air_read(data))
    return jsonify({"error": 'POST {"format": "read", ...}'}), 400


def request_moment():
    """The demo moment this request asked for (?sample=1|2|3 or ?moment=<key>), or the default one."""
    from demo.moments import moment_for
    try:
        return moment_for(request.args.get("moment"), request.args.get("sample"))
    except RuntimeError:   # outside a request
        return moment_for(None)


def monitor_missing():
    """True when this request asked for a public monitor that can't be shown. Demo only: demo/data.py answers it."""
    return False


def request_monitor():
    """The public monitor this request asked for (?monitor=, a positive whole number), or None."""
    try:
        raw = request.args.get("monitor", "")
    except RuntimeError:   # outside a request
        return None
    ok = raw.isascii() and raw.isdigit() and len(raw) <= MONITOR_ID_MAX_DIGITS
    return int(raw) if ok and int(raw) > 0 else None


@app.route("/api/monitors")
def monitors():
    """The demo's monitor picker: the public outdoor monitors nearest ?lat=&lon= (rounded again to 2 decimals, about
    1 km), or nearest New York without them, within publicmap.PICKER_KM: one area request on AirGradient's map per
    place, asked only when someone opens the picker or searches. Demo only."""
    if MODE != "demo":
        return jsonify({"error": "not found"}), 404
    import publicmap
    from demo import public
    lat, lon = request.args.get("lat"), request.args.get("lon")
    if lat is None and lon is None:
        near, lat, lon = public.DEFAULT_LIST
    else:
        try:
            lat, lon = round(float(lat), 2), round(float(lon), 2)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "lat and lon must be a place's coordinates"}), 400
        near = "you"
    try:
        rows = demo_data.picker_area(lat, lon)
    except demo_data.WORLD_ERRORS:
        return jsonify({"error": public.WORLD_DOWN}), 502
    return jsonify({"near": near, "monitors": publicmap.nearest(rows, lat, lon)})


import mcp_server   # /mcp for AI assistants and /api/agent/* for the skill (agent_tools.py: the tools and their answers)
mcp_server.init(app, lambda: {**page_view(), "real_clock": page_clock() is None})   # a lambda: the demo replaces page_view below


ALERTS = None   # the alert check on a timer (alerts.py); None unless Home Air reads the home and has push keys
ALERT_STORE = None   # the phones' store, made on first use once the push keys are set (never in the demo)
_alert_store_lock = threading.Lock()


def alert_store_now():
    """The phones' store (alert_store.py), or None until push keys are set and always in the demo."""
    global ALERT_STORE
    import alert_store
    import alerts
    with _alert_store_lock:
        if ALERT_STORE is None and MODE != "demo" and alerts.push_keys_set():
            ALERT_STORE = alert_store.AlertStore(alert_store.alerts_path())
        return ALERT_STORE


def reads_home():
    """Whether Home Air has a reading source for the alerts: a token (live), or the monitors read on the Wi-Fi (the
    poller runs, no token needed). Never the demo or a config with nothing to read."""
    return MODE == "live" or (MODE == "local" and POLLER is not None)


def start_alerts():
    """Check for alerts every few minutes, from the same Summary as the page. Only where Home Air reads the home, with
    both push keys (setup makes them); under gunicorn's one worker, so each alert is sent once. Again after a token is
    saved: a copy that had nothing to read then starts."""
    global ALERTS
    import alerts
    if ALERTS or not reads_home() or not alerts.push_keys_set():
        return
    ALERTS = alerts.Checker(app, alert_store_now(), tz=HOME_TZ or timezone.utc)
    ALERTS.start()


import alerts
alerts.init(app, alert_store_now, demo=lambda: MODE == "demo", reading=reads_home, tz=HOME_TZ or timezone.utc)   # /api/alerts: turn alerts on, choose, test, turn off
start_alerts()


if MODE == "demo":   # every outside source becomes made-up data, generated per request (demo/data.py)
    from demo import data as demo_data
    demo_data.install(sys.modules[__name__], request_moment)

# Compatibility for tools/replay.py and the unchanged golden test.
from sources import api_get, local_now
from air_read import road_traffic, rush_windows


def write_pid_file():
    """Leave this copy's pid in app.pid while it runs, so `setup.py --start-at-login on` can stop it before the system
    starts its own (scripts/autostart.py); removed at exit, SIGTERM included."""
    import atexit, signal
    path = Path(__file__).parent / "app.pid"
    pid = str(os.getpid())
    path.write_text(pid)

    def remove():
        try:
            if path.read_text().strip() == pid:
                path.unlink()
        except OSError:
            pass
    atexit.register(remove)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # atexit runs on SIGTERM only through a normal exit


if __name__ == "__main__":
    write_pid_file()
    port = int(os.environ.get("PORT", 5555))
    host = os.environ.get("HOST", "127.0.0.1")   # HOST=0.0.0.0 to open it from a phone on the same network
    print(f"Home Air → http://localhost:{port}")
    print_setup_link(port)
    app.run(host=host, port=port, debug=False)
