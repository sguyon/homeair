"""The roads near a home: the geometry helpers (flat local metres, distance to a segment, compass bearing, distance on
the globe) and the rule that picks the main roads from a metro's road file. Standard library only."""
import gzip
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

EARTH_R_M = 6371008.8  # mean Earth radius in metres
FIND_M = 1000  # roads listed: those within this distance of the place, in metres
REACH_M = 500  # a motorway or trunk road this close is followed, and `toward` holds the bearings of its points this close
PRIMARY_MAX_M = 200  # a primary road is followed only this close
CALM_REACH_M = 2000  # `toward_calm` holds the bearings of a road's points this close, for calm mornings
ARC_CAP = 180  # an arc wider than this degrees is "on several sides": (0, 360)
ROADS_MAX = 4  # roads followed
LIST_MAX = 6  # roads listed, followed or not
DENSIFY_M = 20  # a segment is cut into points this far apart before the bearings are taken
BUCKET_DEG = 0.01  # the file's index cell, in degrees (about 1.1 km)
M_PER_DEG = 111000  # metres in a degree of latitude, to size the index cells around a place
SECTORS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")  # the 8-point names, each centred on its bearing (0, 45, 90, ...)
SECTOR_DEG = 45  # the width of one sector
HIGHWAY_CLASSES = ("motorway", "trunk")  # the classes called "highway"; any other kept class is a "main road"
CLASS_ORDER = {"motorway": 0, "trunk": 1, "primary": 2}  # the order of ties in distance
DEFAULT_RUSH = ((7, 10, "morning"), (16, 20, "evening"))  # (from hour, to hour, name) windows used outside the US


def local_xy(lat0: float, lon0: float) -> Callable[[float, float], tuple[float, float]]:
    """A function (lon, lat) -> (metres east, metres north) of the point (lat0, lon0): flat, good within a few km."""
    k = math.pi / 180 * EARTH_R_M
    kx = k * math.cos(math.radians(lat0))

    def to_xy(lon: float, lat: float) -> tuple[float, float]:
        return (lon - lon0) * kx, (lat - lat0) * k
    return to_xy


def seg_distance(p, a, b) -> tuple[float, tuple[float, float]]:
    """Distance from point p to the segment a-b (all (x, y) in metres), and the nearest point on the segment."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    len2 = dx * dx + dy * dy
    t = 0.0 if len2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / len2))
    nx, ny = ax + t * dx, ay + t * dy
    return math.hypot(p[0] - nx, p[1] - ny), (nx, ny)


def bearing_deg(x: float, y: float) -> float:
    """Compass bearing of the vector (x east, y north) in degrees: 0 = north, 90 = east, clockwise, in [0, 360)."""
    return math.degrees(math.atan2(x, y)) % 360


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two points on the globe."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


# ---- the road file ----------------------------------------------------------------------------------------

class RoadFile:
    """One metro's road file, its pieces in an index of BUCKET_DEG cells. `roads` is the file's table of groups."""

    def __init__(self, name: str, country: str, roads: dict, features: list[dict]):
        self.name, self.country, self.roads, self.features = name, country, roads, features
        self.buckets: dict[tuple[int, int], list[int]] = {}
        for i, f in enumerate(features):
            xs = [c[0] for c in f["geometry"]["coordinates"]]
            ys = [c[1] for c in f["geometry"]["coordinates"]]
            for bx in range(self._cell(min(xs)), self._cell(max(xs)) + 1):
                for by in range(self._cell(min(ys)), self._cell(max(ys)) + 1):
                    self.buckets.setdefault((by, bx), []).append(i)

    @staticmethod
    def _cell(deg: float) -> int:
        return math.floor(deg / BUCKET_DEG)

    @classmethod
    def load(cls, path: Path) -> "RoadFile":
        path = Path(path)
        with gzip.open(path, "rt", encoding="utf-8") as f:
            doc = json.load(f)
        return cls(path.name, doc.get("country", ""), doc["roads"], doc["features"])

    def near(self, lat: float, lon: float, radius_m: float):
        """The pieces that may lie within radius_m of the place (a superset: whole cells)."""
        dlat = radius_m / M_PER_DEG
        dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
        seen = set()
        for by in range(self._cell(lat - dlat), self._cell(lat + dlat) + 1):
            for bx in range(self._cell(lon - dlon), self._cell(lon + dlon) + 1):
                for i in self.buckets.get((by, bx), ()):
                    if i not in seen:
                        seen.add(i)
                        yield self.features[i]


@dataclass(frozen=True)
class Found:
    key: str
    name: str            # as shown, with "the" where it takes one
    short: str           # the name without a leading "the "
    kind: str            # "highway" or "main road"
    cls: str             # the highest class among the road's pieces within FIND_M
    tier: int            # 1 to 4 (4 is listed and never followed)
    band: str            # "within 200 m", "within 500 m" or "500 m to 1 km"
    side: str            # the 8-point name of the bearing to the road's nearest point
    toward: tuple[int, int] | None   # the arc of bearings to the road's points within REACH_M
    toward_calm: tuple[int, int]     # the same within CALM_REACH_M
    aadt: int | None
    trucks_pct: int | None
    followed: bool
    slow: tuple[tuple[int, int], ...] = ()   # weekday [from, to) hours measured slow near the place (slow_near; NYC only)


# ---- bearings, arcs, sectors ------------------------------------------------------------------------------

def side_name(bearing: float) -> str:
    """The 8-point name of a bearing in degrees."""
    return SECTORS[int(((bearing + SECTOR_DEG / 2) % 360) // SECTOR_DEG)]


def arc(bearings: list[float]) -> tuple[int, int] | None:
    """The smallest arc (lo, hi) in whole degrees holding every bearing, clockwise from lo to hi (lo > hi across north).
    None for no bearing; (0, 360) when the smallest arc is wider than ARC_CAP ("on several sides")."""
    if not bearings:
        return None
    b = sorted({x % 360 for x in bearings})
    gaps = [((b[(i + 1) % len(b)] - b[i]) % 360 or 360, i) for i in range(len(b))]   # the gap after b[i]; a lone bearing has 360
    gap, i = max(gaps)
    start, end = b[(i + 1) % len(b)], b[i]
    if 360 - gap > ARC_CAP:
        return (0, 360)
    lo, hi = math.floor(start) % 360, math.ceil(end) % 360   # rounded outwards: the arc still holds every bearing
    return (lo, hi)


def _in_arc(a: tuple[int, int], bearing: float) -> bool:
    a = tuple(a)   # config.toml gives a list
    if a == (0, 360):
        return True
    lo, hi = a
    bearing %= 360
    return lo <= bearing <= hi if lo <= hi else bearing >= lo or bearing <= hi


def sectors(a: tuple[int, int]) -> list[str]:
    """The sector names (each 45 degrees wide, centred on its name) that the arc touches, in compass order."""
    a = tuple(a)   # config.toml gives a list
    if a == (0, 360):
        return list(SECTORS)
    width = (a[1] - a[0]) % 360
    out = []
    for k, name in enumerate(SECTORS):
        start = (k * SECTOR_DEG - SECTOR_DEG / 2) % 360   # the sector is [start, start + 45)
        if (start - a[0]) % 360 <= width or (a[0] - start) % 360 < SECTOR_DEG:
            out.append(name)
    return out


def reaches(toward: tuple, bearing: float) -> bool:
    """Whether a bearing in degrees is inside the arc (lo, hi), or inside one of the sector names."""
    if toward and isinstance(toward[0], str):
        return side_name(bearing) in toward
    return _in_arc(toward, bearing)


# ---- slow hours -------------------------------------------------------------------------------------------

def union_windows(lists) -> tuple[tuple[int, int], ...]:
    """The [from, to) hour windows of several lists merged: [[7, 9]] and [[8, 12], [15, 16]] -> ((7, 12), (15, 16))."""
    hours = sorted({h for windows in lists for a, b in windows for h in range(a, b)})
    out = []
    for h in hours:
        if out and out[-1][1] == h:
            out[-1][1] = h + 1
        else:
            out.append([h, h + 1])
    return tuple((a, b) for a, b in out)


def _line_within(line: list, to_xy, max_m: float) -> bool:
    xy = [to_xy(*c[:2]) for c in line]
    if len(xy) == 1:
        return math.hypot(*xy[0]) <= max_m
    return any(seg_distance((0.0, 0.0), a, b)[0] <= max_m for a, b in zip(xy, xy[1:]))


def slow_near(meta: dict, to_xy) -> tuple[tuple[int, int], ...]:
    """A road's slow hours for a home: the union of the slow windows of its speed links that pass within FIND_M of the
    home (the stretch the home is near), else the whole road's `slow`, else none (the class defaults apply)."""
    near = [link for link in meta.get("links") or () if link.get("line") and _line_within(link["line"], to_xy, FIND_M)]
    if near:
        return union_windows(link.get("slow") or () for link in near)
    return tuple(tuple(w) for w in meta.get("slow") or ())


# ---- the rule ---------------------------------------------------------------------------------------------

def _tier(cls: str, dist: float) -> int:
    if cls in HIGHWAY_CLASSES:
        return 1 if dist <= REACH_M else 3
    return 2 if dist <= PRIMARY_MAX_M else 4


def _band(tier: int, dist: float) -> str:
    if tier == 1:
        return "within 500 m"
    if tier == 2:
        return "within 200 m"
    if tier == 3:
        return "500 m to 1 km"
    return "within 500 m" if dist <= REACH_M else "500 m to 1 km"


def _bearings(xy: list, max_m: float) -> list[float]:
    """Bearings from the origin to the points of a line (cut every DENSIFY_M) that are within max_m."""
    out = []
    for a, b in zip(xy, xy[1:]):
        n = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / DENSIFY_M))
        for k in range(n + 1):
            x, y = a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n
            if 0 < math.hypot(x, y) <= max_m:
                out.append(bearing_deg(x, y))
    return out


def find_roads(lat: float, lon: float, data: RoadFile) -> list[Found]:
    """The main roads within FIND_M of a place, at most LIST_MAX, in selection order: tier, distance, class, name.
    The first ROADS_MAX of tiers 1 to 3 are followed. Ramps, tunnels and pieces in no group are left out."""
    to_xy = local_xy(lat, lon)
    groups: dict[str, dict] = {}
    for f in data.near(lat, lon, CALM_REACH_M):
        p = f["properties"]
        key = p.get("g")
        if key is None or p.get("l") or p.get("t"):
            continue
        xy = [to_xy(*c) for c in f["geometry"]["coordinates"]]
        near_d, near_pt = min((seg_distance((0.0, 0.0), a, b) for a, b in zip(xy, xy[1:])), key=lambda r: r[0])
        g = groups.setdefault(key, {"d": math.inf, "pt": (0.0, 0.0), "cls": None, "lines": []})
        g["lines"].append(xy)
        if near_d <= FIND_M:
            if CLASS_ORDER[p["c"]] < CLASS_ORDER.get(g["cls"], len(CLASS_ORDER)):
                g["cls"] = p["c"]
            if near_d < g["d"]:
                g["d"], g["pt"] = near_d, near_pt
    cands = []
    for key, g in groups.items():
        if g["cls"] is None:
            continue
        meta = data.roads[key]
        tier = _tier(g["cls"], g["d"])
        near_b = [x for ln in g["lines"] for x in _bearings(ln, REACH_M)]
        calm_b = [x for ln in g["lines"] for x in _bearings(ln, CALM_REACH_M)]
        name = meta["name"]
        cands.append((tier, g["d"], CLASS_ORDER[g["cls"]], name.casefold(), Found(
            key=key, name=name, short=name.removeprefix("the "),
            kind="highway" if g["cls"] in HIGHWAY_CLASSES else "main road", cls=g["cls"], tier=tier,
            band=_band(tier, g["d"]), side=side_name(bearing_deg(*g["pt"])),
            toward=arc(near_b), toward_calm=arc(calm_b) or (0, 360),
            aadt=meta.get("aadt"), trucks_pct=meta.get("trucks_pct"), followed=False,
            slow=slow_near(meta, to_xy))))
    cands.sort(key=lambda c: c[:4])
    out, followed = [], 0
    for c in cands[:LIST_MAX]:
        f = c[4]
        if f.tier <= 3 and followed < ROADS_MAX:
            followed += 1
            f = Found(**{**f.__dict__, "followed": True})
        out.append(f)
    return out


def entry(found: Found, data_file: str, country: str) -> dict:
    """The [[roads]] values for a found road. A far road (no arc within REACH_M) writes no `toward`; `slow` only when the
    file holds measured slow hours."""
    e = {"name": found.name, "kind": found.kind, "key": found.key, "data": data_file, "country": country,
         "band": found.band, "side": found.side}
    if found.toward is not None:
        e["toward"] = list(found.toward)
    e["toward_calm"] = list(found.toward_calm)
    if found.aadt is not None:
        e["aadt"] = found.aadt
    if found.trucks_pct is not None:
        e["trucks_pct"] = found.trucks_pct
    if found.slow:
        e["slow"] = [list(w) for w in found.slow]
    e["found"] = "auto"
    e["on"] = found.followed
    return e
