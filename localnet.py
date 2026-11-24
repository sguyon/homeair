"""Reading the home's AirGradient monitors on its Wi-Fi: finding them (mDNS), the local read, the mapper to the cloud
row's shape, and the poller that reads each monitor every minute and finds it again when it moves.

Nothing here writes config.toml or the store: addresses stay in memory (the poller can also keep the last good one in
address_memory's file), and logs name a monitor's kind and an error's
class, never an address."""

import dataclasses
import errno
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

import budgets
import config
import token_setup

log = logging.getLogger("aq.local")
logging.getLogger("zeroconf").setLevel(logging.ERROR)   # its debug lines carry addresses

LOCAL_IDS = {"indoor": 1, "outdoor": 2}   # a monitor's id before its cloud id is known; not derived from the serial
# Where a monitor's reading comes from, in the words the page, `setup.py --live` and `--status` share.
WHERE = {"local": "on your Wi-Fi", "cloud": "cloud", "stale": "not answering"}
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"   # the row's timestamp: UTC, seconds

# Local key -> the key it becomes in the cloud row's shape.
RENAMED = {"pm02Compensated": "pm02_corrected", "atmpCompensated": "atmp_corrected",
           "rhumCompensated": "rhum_corrected", "firmware": "firmwareVersion"}
KEPT = ("pm01", "pm02", "pm10", "pm003Count", "rco2", "atmp", "rhum", "tvocIndex", "noxIndex", "wifi", "model",
        "serialno")
TEXT_KEYS = ("model", "serialno", "firmware")   # strings; every other kept key is a number
# What a believable value is, per local key: a value outside is dropped from the row (a failing sensor).
PM_RANGE = (0, 1000)        # µg/m³ for pm01, pm02, pm10 and pm02Compensated
RANGES = {
    "pm01": PM_RANGE, "pm02": PM_RANGE, "pm10": PM_RANGE, "pm02Compensated": PM_RANGE,
    "pm003Count": (0, 100000),   # particles per 0.1 L
    "rco2": (0, 10000),          # ppm
    "atmp": (-40, 85), "atmpCompensated": (-40, 85),   # °C, the sensor's rated span
    "rhum": (0, 100), "rhumCompensated": (0, 100),     # % relative humidity
    "tvocIndex": (0, 500), "noxIndex": (0, 500),       # Sensirion's index scale
    "wifi": (-120, 0),           # dBm
}


@dataclasses.dataclass(frozen=True)
class Monitor:
    kind: str            # "indoor" | "outdoor"
    serial: str          # lowercase
    name: str
    location_id: int     # the cloud id, else LOCAL_IDS[kind]
    host: str | None     # a fixed address (the home-server copy), else None
    port: int = 80


def _number(value, key):
    """The value when it is a number inside its key's range, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    low, high = RANGES[key]
    return value if low <= value <= high else None


def map_reading(payload, mon, now):
    """A local /measures/current reply as the cloud row's shape; None when it isn't this monitor's or has no
    compensated PM2.5 (firmware older than 3.1.4)."""
    serial = payload.get("serialno")
    if not isinstance(serial, str) or serial.lower() != mon.serial:
        return None
    if _number(payload.get("pm02Compensated"), "pm02Compensated") is None:
        return None
    row = {}
    for key in KEPT + tuple(RENAMED):
        if key not in payload:
            continue
        value = payload[key]
        if key in TEXT_KEYS:
            value = value if isinstance(value, str) else None
        else:
            value = _number(value, key)
        if value is not None:
            row[RENAMED.get(key, key)] = value
    row["serialno"] = serial.lower()
    row["timestamp"] = now.astimezone(timezone.utc).strftime(TIME_FORMAT)
    row["locationId"], row["locationName"], row["locationType"] = mon.location_id, mon.name, mon.kind
    row["via"] = "local"
    return row


# --- the read ----------------------------------------------------------------------------------------------------

CONNECT_TIMEOUT_S = 2       # s: the real outdoor monitor (weaker Wi-Fi) took 0.2-1.0 s to accept and timed out 1 in 5 at 1 s (22 Nov)
READ_TIMEOUT_S = 2          # s: it answers in about 0.2 s once connected; connect + read stays within the spec's 4 s
MAX_BODY_BYTES = 16 * 1024  # bytes: a reply is about 500; anything bigger isn't a monitor
CHUNK_BYTES = 2048          # bytes: read size while counting toward MAX_BODY_BYTES


class LocalReadError(Exception):
    """A read that gave nothing usable. The message never holds an address or a URL."""


NO_ROUTE = "no route to host"   # LocalReadError's message when the connection ended in EHOSTUNREACH


def _no_route(error):
    """Whether a failed connection ended in "No route to host" (EHOSTUNREACH), anywhere in requests' and urllib3's
    chain of errors. On a Mac it is what every local connection gets while the process lacks macOS's Local Network
    permission (22 Nov, real home: errno 65 at once, while the OS still resolved the monitor's name)."""
    stack, seen = [error], set()
    while stack:
        e = stack.pop()
        if not isinstance(e, BaseException) or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, OSError) and e.errno == errno.EHOSTUNREACH or "No route to host" in str(e):
            return True
        stack += [e.__cause__, e.__context__, getattr(e, "reason", None), *e.args]
    return False


TOTAL_READ_SECONDS = CONNECT_TIMEOUT_S + READ_TIMEOUT_S   # s: a whole read, checked as each piece arrives, so a trickle ends at the first piece past it (at most READ_TIMEOUT_S late)


def _get_json(address, port, path, get=None):
    """GET http://address:port/path and return its JSON object; LocalReadError on anything else. `get` is a
    requests-style function; the default is a session that ignores HTTP_PROXY, the system proxy and .netrc, since
    this address is on the home's own network."""
    began = time.monotonic()
    session = None
    if get is None:
        session = requests.Session()
        session.trust_env = False
        get = session.get
    try:
        try:
            response = get(f"http://{address}:{port}{path}", timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
                           allow_redirects=False, stream=True, headers={"Accept-Encoding": "identity"})
        except requests.RequestException as error:
            raise LocalReadError(NO_ROUTE if _no_route(error) else type(error).__name__) from None
        try:
            if response.status_code != 200:
                raise LocalReadError(f"status {response.status_code}")
            if "json" not in response.headers.get("Content-Type", "").lower():
                raise LocalReadError("not JSON")
            body = b""
            while True:
                chunk = response.raw.read1(CHUNK_BYTES)   # returns what has arrived: each step waits at most READ_TIMEOUT_S
                if not chunk:
                    break
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    raise LocalReadError("too big")
                if time.monotonic() - began > TOTAL_READ_SECONDS:
                    raise LocalReadError("too slow")
        except (requests.RequestException, OSError, urllib3.exceptions.HTTPError) as error:
            raise LocalReadError(type(error).__name__) from None
        finally:
            response.close()
    finally:
        if session:
            session.close()
    try:
        data = json.loads(body)
    except ValueError:
        raise LocalReadError("not JSON") from None
    if not isinstance(data, dict):
        raise LocalReadError("not an object")
    return data


def read_current(mon, address, port, get=None):
    """The monitor's raw /measures/current reply (a dict). No token; never a redirect. Whether it is this monitor's
    and complete is map_reading's call."""
    return _get_json(address, port, "/measures/current", get)


def read_config(address, port):
    """What setup needs from the monitor's /config: its temperature unit and country. Read once, at setup."""
    data = _get_json(address, port, "/config")
    return {key: data.get(key) for key in ("temperatureUnit", "country")}


# --- discovery ---------------------------------------------------------------------------------------------------

SERVICE = "_airgradient._tcp.local."
BROWSE_SECONDS = 3.0        # s: a monitor on the same Wi-Fi answers in about 1 s; this is the most setup waits
BROWSE_GRACE_SECONDS = 0.3  # s: past BROWSE_SECONDS, how long discover() still waits for the browser thread
BROWSE_LISTEN_SECONDS = 2.0  # s: how long to collect answers before resolving them; on the real home the second monitor answered at 1.0 s (22 Nov), so 1 s missed it
OS_LOOKUP_SECONDS = 1.0     # s: the OS resolver's limit for one monitor's name; it answered in 0.03 s when mDNS browsing missed the weaker-Wi-Fi monitor (22 Nov)
MIN_FIRMWARE = (3, 1, 4)    # from here the monitor sends pm02Compensated
FOUND_FILE_ENV = "AIRGRADIENT_LOCAL_FOUND"   # a JSON list of {serial, model, firmware, address, port}: simulated setups
MODEL_KINDS = {"I-": "indoor", "O-": "outdoor"}


@dataclasses.dataclass(frozen=True)
class Found:
    serial: str     # lowercase
    model: str
    firmware: str
    address: str    # IPv4
    port: int
    via: str = "mdns"   # how it was found: "mdns" (the browse) or "name" (the OS lookup of its .local name)


def kind_of(model):
    """"indoor" for I-…, "outdoor" for O-…, else None."""
    return next((kind for prefix, kind in MODEL_KINDS.items() if str(model).startswith(prefix)), None)


def _version(text):
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", str(text))
    return tuple(int(part) for part in match.groups()) if match else None


def not_readable(found):
    """None when Home Air reads this monitor, else one line saying why not."""
    if kind_of(found.model) is None:
        return f"{found.model}: Home Air doesn't read this model yet"
    version = _version(found.firmware)
    if version is None or version < MIN_FIRMWARE:
        return f"firmware {found.firmware} is older than 3.1.4: update it in the AirGradient app"
    return None


def environment_blocker(env=os.environ, proc_version=Path("/proc/version")):
    """Why this copy can't browse the home's Wi-Fi: "hosted", "container", "wsl", or None. Hosted first: a host that
    builds from the Dockerfile (Railway) has the container's marker too, and a hosted copy never reads the Wi-Fi."""
    if token_setup.hosted(env):
        return "hosted"
    if token_setup.DOCKER_MARKER.exists() or token_setup.PODMAN_MARKER.exists():
        return "container"
    try:
        if "microsoft" in proc_version.read_text(errors="replace").lower():
            return "wsl"
    except OSError:
        pass
    return None


def _zeroconf_browse(seconds, entries):
    """Browse SERVICE and resolve what answered, appending to `entries` as each resolves: dicts with serial, model,
    firmware, addresses, port. The deadline counts from here, start-up and close included; one name that fails to
    resolve is skipped."""
    deadline = time.monotonic() + seconds
    from zeroconf import IPVersion, ServiceBrowser, Zeroconf
    names = set()
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    try:
        def on_change(zeroconf, service_type, name, state_change):
            names.add(name)

        browser = ServiceBrowser(zc, SERVICE, handlers=[on_change])
        time.sleep(max(0, min(BROWSE_LISTEN_SECONDS, deadline - time.monotonic())))   # collect every answer first
        for name in sorted(names):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                info = zc.get_service_info(SERVICE, name, timeout=int(max(0.2, left) * 1000))
                if info is None:
                    continue
                txt = {key.decode(errors="replace"): (value or b"").decode(errors="replace")
                       for key, value in info.properties.items()}
                entries.append({"serial": txt.get("serialno", ""), "model": txt.get("model", ""),
                                "firmware": txt.get("fw_ver", ""), "addresses": info.parsed_addresses(IPVersion.V4Only),
                                "port": info.port or 80})
            except Exception as error:   # one bad answer must not hide the others
                log.warning("resolving a monitor failed (%s)", type(error).__name__)
        browser.cancel()
    finally:
        zc.close()


def _own_ipv4(text):
    """Whether `text` is an IPv4 address on the home's own network."""
    try:
        return config.is_own_network(ipaddress.IPv4Address(text))
    except ValueError:
        return False


def _port(value):
    """The port as an int from 1 to 65535 (80 when absent), else None."""
    try:
        port = int(value or 80)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _os_resolver(name):
    """The IPv4 addresses the OS resolver (mDNS included) gives `name`."""
    return [info[4][0] for info in socket.getaddrinfo(name, 80, socket.AF_INET, socket.SOCK_STREAM)]


def os_lookup(serial, resolver=_os_resolver, seconds=OS_LOOKUP_SECONDS):
    """The home-network IPv4 address the OS gives airgradient_<serial>.local, else None. The lookup runs in a daemon
    thread and is abandoned after `seconds`, so a resolver that hangs can't hold the caller."""
    if not config.SERIAL_RE.fullmatch(serial):
        return None
    answers = []

    def look():
        try:
            answers.extend(resolver(f"airgradient_{serial}.local"))
        except Exception as error:   # not found, no network: nothing
            log.info("looking up a monitor's name failed (%s)", type(error).__name__)

    thread = threading.Thread(target=look, daemon=True)
    thread.start()
    thread.join(seconds)
    return next((a for a in list(answers) if _own_ipv4(a)), None)


def reach(serial, resolver=_os_resolver, reader=read_current):
    """Whether this process reaches a monitor, by its name alone (no browse): "reads" (the OS finds
    airgradient_<serial>.local and it answers as that serial), "no_route" (found by name, but the connection fails with
    No route to host), "named" (found by name, any other failure) or None (no answer by name). For --live's diagnosis."""
    address = os_lookup(serial, resolver)
    if address is None:
        return None
    try:
        payload = reader(None, address, 80)
    except LocalReadError as error:
        return "no_route" if str(error) == NO_ROUTE else "named"
    except Exception:
        return "named"
    return "reads" if str(payload.get("serialno") or "").lower() == serial else "named"


def _from_file(path):
    return [Found(str(item["serial"]).lower(), item["model"], item["firmware"], item["address"], int(item["port"]))
            for item in json.loads(Path(path).read_text(encoding="utf-8"))]


def discover(seconds=BROWSE_SECONDS, browser=None, env=os.environ, proc_version=Path("/proc/version"), serials=(),
             resolver=_os_resolver, reader=read_current):
    """The AirGradient monitors on this Wi-Fi, sorted by serial; [] after at most about `seconds` when none answer,
    at once in a container, on a hosted copy or in WSL. `browser(seconds)` returns dicts (serial, model, firmware,
    addresses, port); the default browses with zeroconf, IPv4 only. Only a 12-hex serial and an address on the home's own
    network (config.is_own_network) are kept. A serial in `serials` (the home's known monitors) that the browse missed is
    looked up by name with the OS resolver (os_lookup) and kept only when a read at that address answers as that serial
    (`reader`); that adds at most OS_LOOKUP_SECONDS plus one read per missing serial."""
    if env.get(FOUND_FILE_ENV):
        return sorted(_from_file(env[FOUND_FILE_ENV]), key=lambda f: f.serial)
    if environment_blocker(env, proc_version):
        return []
    entries = []

    def browse():
        try:
            if browser is None:
                _zeroconf_browse(seconds, entries)
            else:
                entries.extend(browser(seconds))
        except Exception as error:   # no network, a refused socket: nothing found
            log.warning("browsing for monitors failed (%s)", type(error).__name__)

    thread = threading.Thread(target=browse, daemon=True)
    thread.start()
    thread.join(seconds + BROWSE_GRACE_SECONDS)
    found = []
    for entry in list(entries):
        serial = str(entry.get("serial") or "").lower()
        addresses = [a for a in entry.get("addresses", []) if _own_ipv4(a)]
        port = _port(entry.get("port"))
        if addresses and port and config.SERIAL_RE.fullmatch(serial):
            found.append(Found(serial, entry.get("model", ""), entry.get("firmware", ""), addresses[0], port))
    for serial in dict.fromkeys(str(s).lower() for s in serials):
        if any(f.serial == serial for f in found):
            continue
        address = os_lookup(serial, resolver)
        if address is None:
            continue
        try:
            payload = reader(None, address, 80)
        except Exception as error:   # not answering, or not a monitor
            log.info("reading a monitor found by name failed (%s)", type(error).__name__)
            continue
        if str(payload.get("serialno") or "").lower() == serial:
            found.append(Found(serial, str(payload.get("model") or ""), str(payload.get("firmware") or ""), address, 80,
                               "name"))
    return sorted(found, key=lambda f: f.serial)


# --- the poller --------------------------------------------------------------------------------------------------

LOCAL_READ_SECONDS = 60     # s between reads of one monitor: the firmware measures every 60 s, Home Assistant reads at this rate
FAILS_BEFORE_BROWSE = 3     # reads in a row that fail before looking for the monitor again
PAUSE_SECONDS = 600         # s a monitor that wasn't found again is left alone
LOCAL_FRESH_SECONDS = 90    # s a local reading stays "fresh": a read every 60 s, so one miss ends it and the cloud takes over
RESUME_GAP_SECONDS = 600    # s without a read after which a new one counts as "reads resumed" (the cloud fills the gap)
TICK_SECONDS = 1            # s between the poller's passes over the monitors


class _Slot:
    """What the poller keeps for one monitor, in memory only."""

    def __init__(self, mon):
        self.mon = mon
        self.address, self.port = mon.host, mon.port
        self.next_at = 0.0          # clock time of the next read
        self.fails = 0              # reads in a row that failed
        self.last_ok = None         # wall-clock time (UTC) of the last good read: the monotonic clock stops in sleep
        self.row = None             # its mapped row
        self.busy = False           # a read is in flight


class Poller:
    """Reads each monitor every LOCAL_READ_SECONDS. `read(mon, address, port)` returns the raw reply, `find()` the
    monitors on the Wi-Fi; `browse` is False where mDNS can't work (a container), where a monitor is read only at its
    configured host. `clock` (monotonic) only schedules reads; the gap since the last read and a reading's freshness
    are measured with `utcnow`, because the monotonic clock stops while the computer sleeps."""

    def __init__(self, monitors, browse, on_row=lambda row: None, on_resume=lambda mon, gap_s: None,
                 read=read_current, find=discover, clock=time.monotonic,
                 utcnow=lambda: datetime.now(timezone.utc), memory=None):
        self.browse, self.on_row, self.on_resume = browse, on_row, on_resume
        self._read, self._find, self._clock, self._utcnow = read, find, clock, utcnow
        self._memory = memory   # address_memory.AddressMemory or None: the last good address, kept across restarts
        self._slots = {mon.kind: self._new_slot(mon) for mon in monitors}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._seen = []          # the Found monitors of the last browse, for others(); in memory only
        self._browsed = False    # whether a browse has run since start

    def _new_slot(self, mon):
        slot = _Slot(mon)
        if slot.address is None and self._memory:   # tried first, before any browse
            remembered = self._memory.get(mon.serial)
            if remembered:
                slot.address, slot.port = remembered
        return slot

    def use(self, monitors):
        """Read these monitors from now on (the config the app wrote after a token, with the cloud ids). A monitor
        already read keeps its address, its freshness and its last reading, which takes the new id and name; one with
        another serial starts afresh."""
        with self._lock:
            slots = {}
            for mon in monitors:
                slot = self._slots.get(mon.kind)
                if slot is None or slot.mon.serial != mon.serial or slot.mon.host != mon.host:
                    slot = self._new_slot(mon)
                else:
                    slot.mon = mon
                    if slot.row is not None:
                        slot.row = {**slot.row, "locationId": mon.location_id, "locationName": mon.name}
                slots[mon.kind] = slot
            self._slots = slots

    def latest(self, kind):
        with self._lock:
            slot = self._slots.get(kind)
            return slot.row if slot else None

    def state(self, kind):
        """"local" when the last read succeeded less than LOCAL_FRESH_SECONDS ago, else "stale"."""
        with self._lock:
            slot = self._slots.get(kind)
            fresh = slot and slot.last_ok is not None and \
                0 <= (self._utcnow() - slot.last_ok).total_seconds() < LOCAL_FRESH_SECONDS
        return "local" if fresh else "stale"

    def others(self):
        """The monitors the last browse found that aren't read, by kind, for "Your monitors": the last 4 characters of
        each serial and its model, never an address or a whole serial (/health shows neither). Only ones Home Air reads."""
        with self._lock:
            read = {slot.mon.serial for slot in self._slots.values()}
            seen = list(self._seen)
        out = {}
        for found in seen:
            kind = kind_of(found.model)
            if kind and found.serial not in read and not_readable(found) is None:
                out.setdefault(kind, []).append({"serial_end": found.serial[-4:], "model": found.model})
        return out

    def _browse(self, serials):
        found = self._find(serials=serials)
        with self._lock:
            self._seen, self._browsed = list(found), True
        return found

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="localnet-poller").start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
                if self.browse and not self._browsed:   # once, after the first reads: who else is on the Wi-Fi
                    self._browse(())
            except Exception as error:   # a bug must not end the thread that keeps the readings coming
                log.error("poller pass failed (%s)", type(error).__name__)
            self._stop.wait(TICK_SECONDS)

    def tick(self):
        """One pass: read every monitor that is due."""
        for slot in list(self._slots.values()):
            with self._lock:
                if slot.busy or self._clock() < slot.next_at:
                    continue
                slot.busy = True
            try:
                self._poll(slot)
            finally:
                slot.busy = False

    def _look_for(self, slot):
        """Look for the monitor by serial (a browse, then the OS lookup of its name): True when found (its address is
        updated), else pause it. The address it had is kept: only a failed read ever replaces it."""
        if self.browse:
            for found in self._browse([slot.mon.serial]):
                if found.serial == slot.mon.serial:
                    slot.address, slot.port, slot.fails = found.address, found.port, 0
                    slot.next_at = self._clock()
                    return True
        slot.next_at = self._clock() + PAUSE_SECONDS
        log.info("%s monitor not found on the Wi-Fi: paused for %d s", slot.mon.kind, PAUSE_SECONDS)
        return False

    def _poll(self, slot):
        kind, now = slot.mon.kind, self._clock()
        if slot.address is None and not self._look_for(slot):
            return
        if slot.address is None:
            return
        slot.next_at = now + LOCAL_READ_SECONDS
        try:
            budgets.spend("ag_local")
            payload = self._read(slot.mon, slot.address, slot.port)
            wall = self._utcnow()
            row = map_reading(payload, slot.mon, wall)
            if row is None:
                raise LocalReadError("not this monitor's reply")
        except budgets.BudgetExceeded:
            return
        except Exception as error:
            slot.fails += 1
            log.warning("%s monitor read failed (%s), %d in a row", kind, type(error).__name__, slot.fails)
            if slot.fails >= FAILS_BEFORE_BROWSE:
                self._look_for(slot)
            return
        gap = None if slot.last_ok is None else (wall - slot.last_ok).total_seconds()
        with self._lock:
            slot.row, slot.last_ok, slot.fails = row, wall, 0
        if self._memory and slot.mon.host is None:
            self._memory.remember(slot.mon.serial, slot.address, slot.port)
        self.on_row(row)
        if gap is not None and gap > RESUME_GAP_SECONDS:
            self.on_resume(slot.mon, gap)
