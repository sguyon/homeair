"""The road data on this computer: the data folder, the published index.json, downloads checked by sha256, the retry
when a download failed, the roads written to config.toml at setup (setup_roads), and the map's route cut around the
rounded place. Road-data requests use ROADS_BASE: when setup looks up roads for a saved place (not a bare `./homeair setup` or a profile-only change) and saved roads aren't kept, Home Air fetches a fresh index during
setup even when one is cached. It downloads an area's file only when a matching index entry supplies a sha256 and no cached
file matches. Normal operation makes no road-data request. At start, a failed setup download is retried only with
a config file, outside hosted environments and containers, when the pending marker makes a retry due (at most
once a day). Missing files named by saved automatic roads are downloaded, with an index request if a trusted
digest is missing.
Saved filenames are kept; an app-version change does not select a newer data file."""
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import tomllib
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import upstream
from config import ROOT, ROAD_FOUND, ConfigError, backup_config, load_config, replace_text, set_auto_roads
from roads import RoadFile, entry, find_roads, haversine_m, local_xy

ROADS_BASE = os.environ.get("HOMEAIR_ROADS_BASE", "https://github.com/sguyon/homeair/releases/download/data-v1")  # the data host: the road-data release this version reads
DOWNLOAD_TIMEOUT = 20  # seconds to wait for the data host, per GET
RETRY_EVERY_H = 24  # hours between two tries at start after a failed download: at most once a day
ROUTE_CLIP_M = 3000  # the map's route: the road's lines within this many metres of the rounded place
ROUTE_TOLERANCE_M = 1  # metres a cut point may sit past ROUTE_CLIP_M (the tests allow it; the bisection lands inside)
EXACT_DECIMALS = 4  # decimals of the exact place read from config.toml, as scripts/setup.py writes it (about 10 m)
ROAD_FILE_MAX_BYTES = 8_000_000  # a download stops past this size: index.json or a metro/block file (NYC's is about 0.7 MB)
ROAD_JSON_MAX_BYTES = 100_000_000  # a road file stops being read past this size unzipped (NYC's is about 7 MB)
HALF_WORLD_DEG = 180  # degrees: normalize longitudes to [-180, 180), including Alaska's islands
WORLD_DEG = 360  # degrees in a full turn of longitude
CHUNK_BYTES = 65536  # a download is read in pieces this size, to stop at ROAD_FILE_MAX_BYTES
CROSS_STEPS = 40  # halvings of a segment to find where it crosses the route's circle (40: far below a millimetre)
FOLDER_MODE = 0o700  # the data folder: owner only
FILE_MODE = 0o600  # files in it: owner only
DATA_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json\.gz")  # a road file's name: a plain file name, never a path
INDEX_NAME = "index.json"
DIGESTS_NAME = "digests.json"  # trusted index digests by dated filename, retained when index.json changes or is lost
OUTCOME_NAME = "outcome"  # what setup's last look found: "found", "none" (no main road within 1 km) or "not_built" (no data for the area)
PENDING_NAME = "pending"  # the marker of a failed download: empty, or the time of the last try at start
SIDE_WORDS = {"N": "north", "NE": "north-east", "E": "east", "SE": "south-east", "S": "south", "SW": "south-west",
              "W": "west", "NW": "north-west"}  # as static/home.js writes them


class RoadDataError(Exception):
    """The index or a road file couldn't be fetched, checked or written."""


@dataclass
class SetupResult:
    status: str   # "written", "none", "not_built", "failed" or "kept"
    lines: list[str] = field(default_factory=list)   # what setup prints


# ---- the folder and the marker ---------------------------------------------------------------------------

def data_dir(config_path: Path | None, env=os.environ, create: bool = True) -> Path:
    """ROAD_DATA_DIR, else road-data/ beside config.toml, else road-data/ in the app's folder. Created owner-only
    (create=False only names it: a check that must not make a folder)."""
    given = env.get("ROAD_DATA_DIR", "").strip()
    folder = Path(given) if given else (Path(config_path).parent if config_path else ROOT) / "road-data"
    if create:
        _make(folder)
    return folder


def _make(folder: Path):
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True, mode=FOLDER_MODE)
        os.chmod(folder, FOLDER_MODE)


def _write(path: Path, data: bytes):
    """The file whole or not at all: a temporary file beside it, then a rename."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), FILE_MODE)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def mark_pending(folder: Path):
    """Leave the marker: the next start tries again. It holds no place, no hash, nothing about the home."""
    try:
        _make(folder)
        _write(folder / PENDING_NAME, b"")
    except OSError:
        pass


def record_outcome(folder: Path, outcome: str):
    """Leave setup's outcome beside the marker: the words only, nothing about the home."""
    try:
        _make(folder)
        _write(folder / OUTCOME_NAME, outcome.encode())
    except OSError:
        pass


def read_outcome(folder: Path) -> str | None:
    """The outcome setup recorded, or None (setup hasn't looked, or the record can't be read)."""
    try:
        text = (folder / OUTCOME_NAME).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return text if text in ("found", "none", "not_built") else None


def mark_tried(folder: Path, now: datetime):
    """After a try at start that failed too: the next try waits RETRY_EVERY_H."""
    if (folder / PENDING_NAME).exists():
        try:
            _write(folder / PENDING_NAME, now.isoformat().encode())
        except OSError:
            pass


def clear_pending(folder: Path):
    try:
        (folder / PENDING_NAME).unlink(missing_ok=True)
    except OSError:
        pass


def retry_due(folder: Path, now: datetime) -> bool:
    """A download failed and this start may try again: never tried at start yet, or the last try is RETRY_EVERY_H old."""
    try:
        text = (folder / PENDING_NAME).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError):
        return True
    if not text:
        return True
    try:
        tried = datetime.fromisoformat(text)
    except ValueError:
        return True
    aware = lambda t: t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return aware(now) - aware(tried) >= timedelta(hours=RETRY_EVERY_H)


# ---- the index and the files -----------------------------------------------------------------------------

def _get(name: str, get) -> bytes:
    """One GET of ROADS_BASE/<name>, read in pieces and stopped past ROAD_FILE_MAX_BYTES."""
    try:
        resp = get(f"{ROADS_BASE}/{name}", timeout=DOWNLOAD_TIMEOUT, headers=upstream.headers(), stream=True)
        try:
            if resp.status_code != 200:
                raise RoadDataError(f"the data host answered {resp.status_code} for {name}")
            data = bytearray()
            for chunk in resp.iter_content(CHUNK_BYTES):
                data += chunk
                if len(data) > ROAD_FILE_MAX_BYTES:
                    raise RoadDataError(f"{name} is larger than {ROAD_FILE_MAX_BYTES} bytes")
            return bytes(data)
        finally:
            resp.close()
    except requests.RequestException as e:
        raise RoadDataError(f"the data host didn't answer for {name} ({type(e).__name__})") from None


def fetch_index(folder: Path, get=requests.get) -> dict:
    """One GET of index.json, kept in the folder. RoadDataError when it can't be fetched or read."""
    data = _get(INDEX_NAME, get)
    try:
        index = json.loads(data)
    except (ValueError, UnicodeError):
        raise RoadDataError("index.json can't be read") from None
    if not isinstance(index, dict) or not isinstance(index.get("metros"), list):
        raise RoadDataError("index.json has no list of metros")
    try:
        _make(folder)
        shas = {**_shas(cached_index(folder)), **_saved_shas(folder), **_shas(index)}
        _write(folder / DIGESTS_NAME, json.dumps(shas, sort_keys=True).encode())
        _write(folder / INDEX_NAME, data)
    except OSError as e:
        raise RoadDataError(f"index.json can't be written ({type(e).__name__})") from None
    return index


def cached_index(folder: Path) -> dict:
    try:
        index = json.loads((folder / INDEX_NAME).read_text(encoding="utf-8"))
        return index if isinstance(index, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _longitude(lon: float) -> float:
    """Keep valid longitudes exact; wrap the rest to [-180, 180), with +180 represented as -180."""
    return lon if -HALF_WORLD_DEG <= lon < HALF_WORLD_DEG else (lon + HALF_WORLD_DEG) % WORLD_DEG - HALF_WORLD_DEG


def _core_area(box, lat: float, lon: float) -> float | None:
    """Area of a core containing the place, with west/south in and east/north out. Never use the data margin."""
    try:
        w, s, e, n = (float(x) for x in box)
    except (TypeError, ValueError):
        return None
    lon = _longitude(lon)
    return (e - w) * (n - s) if w <= lon < e and s <= lat < n else None


def metro_for(index: dict, lat: float, lon: float) -> dict | None:
    """Measured NYC first, then Counted metros, then regional blocks; smallest core and id settle ties.
    Legacy entries without kind are metros. All selection uses half-open cores, never data margins."""
    choices = []
    for m in index.get("metros", []):
        if not isinstance(m, dict):
            continue
        kind, level = m.get("kind", "metro"), m.get("level", 2)
        if kind == "metro" and level in (1, "measured"):
            priority = 0
        elif kind == "metro" and level in (2, "counted"):
            priority = 1
        elif kind == "block":
            priority = 2
        else:
            continue
        area = _core_area(m.get("box"), lat, lon)
        if area is not None:
            choices.append(((priority, area, str(m.get("id", ""))), m))
    return min(choices, key=lambda choice: choice[0])[1] if choices else None


def _covered_us(index: dict, lat: float, lon: float) -> bool:
    """coverage.US lists built core boxes, including empty blocks omitted from metros. Old indexes promise no coverage."""
    coverage = index.get("coverage", {})
    boxes = coverage.get("US", []) if isinstance(coverage, dict) else []
    return isinstance(boxes, list) and any(_core_area(box, lat, lon) is not None for box in boxes)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _doc(data: bytes) -> dict:
    """A road file's contents, or ValueError (gzip, JSON or shape)."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            raw = stream.read(ROAD_JSON_MAX_BYTES + 1)
    except (OSError, EOFError, zlib.error) as e:
        raise ValueError(type(e).__name__) from None
    if len(raw) > ROAD_JSON_MAX_BYTES:
        raise ValueError(f"larger than {ROAD_JSON_MAX_BYTES} bytes unzipped")
    try:
        doc = json.loads(raw)
    except UnicodeError as e:
        raise ValueError(type(e).__name__) from None
    if not (isinstance(doc, dict) and isinstance(doc.get("roads"), dict) and isinstance(doc.get("features"), list)):
        raise ValueError("not a road file")
    return doc


def ensure_file(name: str, folder: Path, sha256: str | None = None, get=requests.get) -> Path:
    """The road file in the folder: the cached one (checked when it was written), else one GET of ROADS_BASE/<name>,
    its sha256 checked, written whole or not at all. Without a sha256 nothing is downloaded. A cached file whose sha256
    differs is fetched again. RoadDataError on any failure."""
    if not isinstance(name, str) or not DATA_NAME.fullmatch(name):
        raise RoadDataError("the road file's name isn't a plain file name")
    path = folder / name
    try:
        if path.is_file():
            if sha256 is None or _sha(path.read_bytes()) == sha256:
                return path
            path.unlink()
    except OSError as e:
        raise RoadDataError(f"{name} can't be read ({type(e).__name__})") from None
    if not sha256:
        raise RoadDataError(f"index.json gives no sha256 for {name}, so it isn't downloaded")
    data = _get(name, get)
    if _sha(data) != sha256:
        raise RoadDataError(f"{name} doesn't match its sha256 in index.json")
    try:
        _doc(data)
    except ValueError as e:
        raise RoadDataError(f"{name} isn't a road file ({e})") from None
    try:
        _make(folder)
        _write(path, data)
    except OSError as e:
        raise RoadDataError(f"{name} can't be written ({type(e).__name__})") from None
    return path


# ---- reading a cached file -------------------------------------------------------------------------------

_LOADED: dict[Path, tuple] = {}   # path -> ((mtime_ns, size), roads, lines by group): the last file read, for the route
_LOCK = threading.Lock()


def _load(folder: Path, name: str):
    """(roads table, {group: [lines]}) of a cached file, or None when it is missing or unreadable. Ramps and tunnels
    are left out, as find_roads leaves them out."""
    if not isinstance(name, str) or not DATA_NAME.fullmatch(name):
        return None
    path = folder / name
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        with _LOCK:
            hit = _LOADED.get(path)
            if hit and hit[0] == stamp:
                return hit[1], hit[2]
        doc = _doc(path.read_bytes())
        lines: dict[str, list] = {}
        for f in doc["features"]:
            p = f["properties"]
            if p.get("g") is None or p.get("l") or p.get("t"):
                continue
            coords = [[float(c[0]), float(c[1])] for c in f["geometry"]["coordinates"]]
            if len(coords) >= 2:
                lines.setdefault(p["g"], []).append(coords)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    with _LOCK:
        _LOADED.clear()   # one file at a time: a home reads one metro or block
        _LOADED[path] = (stamp, doc["roads"], lines)
    return doc["roads"], lines


def road_info(folder: Path, name: str, key: str) -> dict | None:
    """The road's entry in the file's table (name, class, counts...), or None when the file is missing or unreadable."""
    got = _load(folder, name)
    if got is None:
        return None
    meta = got[0].get(key)
    return meta if isinstance(meta, dict) else None


def _cut(inside, a: list, b: list) -> list:
    """The point of a-b where it leaves the circle, a inside and b outside: the last inside point of CROSS_STEPS halvings."""
    lo, hi = 0.0, 1.0
    for _ in range(CROSS_STEPS):
        mid = (lo + hi) / 2
        if inside([a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid]):
            lo = mid
        else:
            hi = mid
    return [a[0] + (b[0] - a[0]) * lo, a[1] + (b[1] - a[1]) * lo]


def _nearest(to_xy, a: list, b: list) -> list:
    """The point of a-b nearest the circle's centre, in flat metres around it."""
    (ax, ay), (bx, by) = to_xy(*a), to_xy(*b)
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    t = 0.0 if len2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / len2))
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


def _clip(line: list, inside, to_xy) -> list[list]:
    """The pieces of a line inside the circle: inside points kept, a segment cut where it crosses the circle."""
    out, cur = [], []
    if inside(line[0]):
        cur = [line[0]]
    for a, b in zip(line, line[1:]):
        ia, ib = bool(cur), inside(b)
        if ia and ib:
            cur.append(b)
        elif ia:
            cur.append(_cut(inside, a, b))
            out.append(cur)
            cur = []
        elif ib:
            cur = [_cut(inside, b, a), b]
        else:
            m = _nearest(to_xy, a, b)   # both ends outside: the segment may still pass through the circle
            if inside(m):
                out.append([_cut(inside, m, a), _cut(inside, m, b)])
    if cur:
        out.append(cur)
    return [piece for piece in out if len(piece) >= 2]


def route_stretch(folder: Path, name: str, key: str, lat2: float, lon2: float) -> list[list[list[float]]] | None:
    """The road's lines ([lon, lat] points) within ROUTE_CLIP_M of the rounded place (lat2, lon2): a point inside is
    kept, a segment that crosses the circle is cut at the crossing, the rest dropped. None without the file."""
    got = _load(folder, name)
    if got is None:
        return None
    inside = lambda p: haversine_m(lat2, lon2, p[1], p[0]) <= ROUTE_CLIP_M
    to_xy = local_xy(lat2, lon2)
    return [piece for line in got[1].get(key, []) for piece in _clip(line, inside, to_xy)]


# ---- setup -----------------------------------------------------------------------------------------------

def _road_words(e: dict) -> str:
    side = SIDE_WORDS.get(e.get("side"), "")
    return f"{e['name']} ({e.get('kind', 'highway')}, {e.get('band')}" + (f", to the {side}" if side else "") + ")"


def _lines(entries: list[dict]) -> list[str]:
    followed = [e for e in entries if e.get("on")]
    if followed:
        return ["Roads: " + "; ".join(_road_words(e) for e in followed) + '. Turn one off in "Your home" › "Around you".']
    if entries:
        return ['Roads: none near enough to follow; the main roads within 1 km are in "Your home" › "Around you".']
    return ["Roads: no main road within 1 km."]


class _Moved(Exception):
    """config.toml's place changed while the roads were looked up."""


def _exact_place(raw: dict) -> tuple[float, float] | None:
    place = raw.get("place", {})
    try:
        return round(float(place["lat"]), EXACT_DECIMALS), round(float(place["lon"]), EXACT_DECIMALS)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _write_roads(config_path: Path, entries: list[dict], place: tuple[float, float], lock):
    """config.toml with these auto [[roads]] in place of the old ones, a backup first; nothing when the text stays the
    same. Under `lock`, with the file read again: _Moved when its place is no longer `place`, ConfigError when the new
    text wouldn't load or the file can't be read."""
    with lock:
        try:
            text = Path(config_path).read_text(encoding="utf-8")
            if _exact_place(tomllib.loads(text)) != place:
                raise _Moved
        except (UnicodeError, tomllib.TOMLDecodeError):
            raise ConfigError("config.toml can't be read") from None
        new = set_auto_roads(text, entries)
        if new == text:
            return
        load_config({"CONFIG_TOML": new})   # never write a file the app can't start with
        backup_config(config_path)
        replace_text(config_path, new)


def setup_roads(config_path: Path, previous: tuple[float, float] | None, get=requests.get, lock=None) -> SetupResult:
    """Find the home's roads from its exact place and write them to config.toml as [[roads]] with found = "auto": the
    old auto blocks replaced and their switches reset; a hand-set [road] and every other [[roads]] block stay as they
    are, in place. At the same place as `previous` (the exact place before this run) with auto entries already:
    "kept", nothing written and no call. A failed download leaves a marker for the next start ("failed") and removes
    the old auto blocks, which may belong to another place (with none, the file isn't touched). The downloads run
    outside `lock` (a context manager); only the write of config.toml takes it. Nothing derived from the exact place
    is stored outside config.toml."""
    lock = contextlib.nullcontext() if lock is None else lock
    try:
        raw = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return SetupResult("failed", ["Roads: config.toml can't be read, so the roads weren't looked up."])
    place = _exact_place(raw)
    if place is None:
        return SetupResult("none", [])   # no place yet: nothing to look up
    lat, lon = place
    listed = raw.get("roads", [])
    auto = [r for r in listed if isinstance(r, dict) and r.get("found") == ROAD_FOUND] if isinstance(listed, list) else []
    if previous is not None and auto and (round(previous[0], EXACT_DECIMALS), round(previous[1], EXACT_DECIMALS)) == place:
        return SetupResult("kept", _lines(auto))
    lon = _longitude(lon)  # selection and geometry search share this value; keep the saved place unchanged
    folder = data_dir(config_path, create=False)
    found = None   # None: the place is outside the index's built coverage
    try:
        folder = data_dir(config_path)
        index = fetch_index(folder, get)
        metro = metro_for(index, lat, lon)
        if metro is None and _covered_us(index, lat, lon):
            found = []   # a built US core with no road file: no main road within 1 km
        if metro is not None:
            path = ensure_file(metro.get("file"), folder, metro.get("sha256"), get)
            try:
                doc = _doc(path.read_bytes())
                data = RoadFile(path.name, doc.get("country", ""), doc["roads"], doc["features"])
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                path.unlink(missing_ok=True)
                raise RoadDataError(f"{path.name} can't be read") from None
            found = [entry(f, path.name, data.country or str(metro.get("country", ""))) for f in find_roads(lat, lon, data)]
    except (RoadDataError, OSError):
        mark_pending(folder)
        try:
            _write_roads(config_path, [], place, lock)
        except (OSError, ConfigError, _Moved):
            pass
        return SetupResult("failed", ["Roads: couldn't get the road data; Home Air on this computer tries again at its next start, at most once a day."])
    try:
        _write_roads(config_path, found or [], place, lock)
    except _Moved:
        return SetupResult("failed", ["Roads: the place in config.toml changed while the roads were looked up; "
                                      "run ./homeair setup again."])
    except (OSError, ConfigError):
        return SetupResult("failed", ["Roads: the roads found couldn't be written to config.toml; it is unchanged."])
    clear_pending(folder)
    record_outcome(folder, "not_built" if found is None else "found" if found else "none")
    if found is None:
        return SetupResult("not_built", ["Roads: road data isn't built for your area yet."])
    return SetupResult("written" if found else "none", _lines(found))


# ---- at start --------------------------------------------------------------------------------------------

def retry_at_start(config_path: Path, now: datetime, get=requests.get, lock=None) -> SetupResult | None:
    """When a download failed and a try is due: setup_roads again (the place is the one in config.toml; `lock` only
    around the write of config.toml), and the time of this try when it fails too. None when no try is due."""
    folder = data_dir(config_path, create=False)
    if not retry_due(folder, now):
        return None
    res = setup_roads(config_path, None, get, lock)
    if res.status == "failed":
        mark_tried(folder, now)
    return res


def _shas(index: dict) -> dict:
    metros = index.get("metros")
    if not isinstance(metros, list):
        return {}  # a damaged cached index adds no digests and never blocks a fresh one
    return {m["file"]: m["sha256"] for m in metros if isinstance(m, dict)
            and isinstance(m.get("file"), str) and DATA_NAME.fullmatch(m["file"])
            and isinstance(m.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", m["sha256"])}


def _saved_shas(folder: Path) -> dict:
    try:
        doc = json.loads((folder / DIGESTS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    return _shas({"metros": [{"file": name, "sha256": sha} for name, sha in doc.items()]}) if isinstance(doc, dict) else {}


def cache_files(folder: Path, roads, get=requests.get) -> list[str]:
    """One GET for each auto road's file that isn't in the folder (a new folder, a restart on Railway, a file removed),
    checked by its retained dated digest or cached index, else one GET of index.json first. Without a trusted digest
    the file is not downloaded. Never select a replacement filename. Return the names that couldn't be fetched."""
    missing = sorted({r.data for r in roads if r.found == ROAD_FOUND and r.data and not (folder / r.data).is_file()})
    if not missing:
        return []
    shas = {**_shas(cached_index(folder)), **_saved_shas(folder)}
    if any(name not in shas for name in missing):
        try:
            shas = {**shas, **_shas(fetch_index(folder, get))}
        except RoadDataError:
            pass
    failed = []
    for name in missing:
        try:
            ensure_file(name, folder, shas.get(name), get)
        except RoadDataError:
            failed.append(name)
    return failed
