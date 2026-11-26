"""Slow hours from NYC DOT's real-time traffic speeds, joined to the road groups of scripts/roads/build.py.

`fetch_links` and `fetch` read NYC DOT's public speed feed (data.cityofnewyork.us, dataset i4gi-tjb9, no key);
`merge_links` gives each link one line; `join_links` gives each link the road group it runs along; `hourly_medians` and
`slow_windows` turn the weekday readings into slow hours, for each link (the stretch it measures) and for the whole road.
"Slow" stands for stop-and-go traffic. At the same flow a slower road holds more vehicles per stretch (density = flow /
speed, traffic-flow arithmetic). Barth & Boriboonsomsin (2008) studied CO₂, not NO₂ or ultrafine particles.
How the hours are used: docs/roads.md.
Credit: NYC Department of Transportation, through NYC Open Data."""
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the app's roads.py and road_timing.py
from road_timing import SLOW_HOURS, SLOW_SHARE  # noqa: E402
from roads import CLASS_ORDER, haversine_m, local_xy, seg_distance  # noqa: E402

SPEEDS_URL = "https://data.cityofnewyork.us/resource/i4gi-tjb9.json"
SPEEDS_LIMIT = 50000  # rows per request
SPEEDS_MAX_PAGES = 100  # a stop for a feed that never returns a short page
SPEEDS_TIMEOUT_S = 120  # one request may take this long (listing four days of links took 14 s on 24 Nov)
SPEED_WEEKS = 4  # weeks of readings the build reads
SPEED_STALE_DAYS = 7  # with no reading this recent, the build copies the previous file's slow hours
SPEED_JOIN_M = 50  # metres: a point of a link lies on a road when one of the road's pieces is this close (estimate)
SPEED_ALONG_SHARE = 0.5  # a link joins the road it runs along: the road with most of its points on it, at least this share
SPEED_SAMPLE_M = 50  # metres between the points taken along a link for the join
CELL_DEG = 0.005  # the join's index cell, in degrees (about 550 m)
SPEEDS_TRIES = 3  # a request that times out or gets a 5xx answer is sent this many times in all
SPEEDS_RETRY_S = 30  # seconds between two tries (the service answered 503 to one page on 24 Nov)
OUTAGE_SPEED = 0  # mph: a reading at this speed is stop-and-go evidence and kept, unless every reading of a link in an hour is at it (a sensor outage: that hour is dropped for that link)
POLY_PRECISION = 1e5  # the feed's encoded_poly_line: Google's polyline encoding, 5 decimals


def _page(params: dict, get, sleep) -> list[dict]:
    for k in range(SPEEDS_TRIES):
        try:
            resp = get(SPEEDS_URL, params=params, timeout=SPEEDS_TIMEOUT_S)
            resp.raise_for_status()
            return resp.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if k == SPEEDS_TRIES - 1 or (status is not None and status < 500):
                raise
            print(f"speeds: {e.__class__.__name__}, trying again in {SPEEDS_RETRY_S} s", file=sys.stderr)
            sleep(SPEEDS_RETRY_S)


def _get_rows(params: dict, get, sleep=time.sleep) -> list[dict]:
    rows = []
    for page in range(SPEEDS_MAX_PAGES):
        got = _page({**params, "$limit": SPEEDS_LIMIT, "$offset": page * SPEEDS_LIMIT}, get, sleep)
        rows += got
        if len(got) < SPEEDS_LIMIT:
            break
    return rows


LINK_FIELDS = "id,link_name,link_points,encoded_poly_line"


def fetch_links(since: date, get=requests.get) -> list[dict]:
    """The links with a reading since a date: {id, link_name, link_points, encoded_poly_line}, one row per distinct value
    (a link can have several: see merge_links). Grouping the feed's whole history makes the service time out."""
    return _get_rows({"$select": LINK_FIELDS, "$where": f"data_as_of >= '{since:%Y-%m-%d}T00:00:00'",
                      "$group": LINK_FIELDS, "$order": "id"}, get)


def fetch(link_ids: list[str], since: date, get=requests.get) -> list[dict]:
    """The readings of the given links since a date: {id, speed (mph), data_as_of}. `data_as_of` is read as New York
    time (not checked)."""
    ids = ",".join("'" + str(i).replace("'", "") + "'" for i in link_ids)
    return _get_rows({"$select": "id,speed,data_as_of", "$where": f"id in ({ids}) AND data_as_of >= '{since:%Y-%m-%d}T00:00:00'",
                      "$order": "data_as_of,id"}, get)


def last_reading(rows: list[dict]) -> datetime | None:
    """The time of the newest reading, or None."""
    times = [_time(r) for r in rows]
    return max((t for t in times if t is not None), default=None)


def _time(row: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(row["data_as_of"])[:19])
    except (KeyError, ValueError):
        return None


def hourly_medians(rows: list[dict]) -> dict[str, dict[int, float]]:
    """Each link's median speed by hour of the day, weekdays only. A row whose time or speed can't be read, or whose
    speed is below 0, is left out. Readings at OUTAGE_SPEED (0) count, except in an hour where every reading of the
    link is at it: that hour is a sensor outage and left out for that link."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        t = _time(r)
        try:
            v = float(r["speed"])
        except (KeyError, TypeError, ValueError):
            continue
        if t is None or t.weekday() >= 5 or not v >= OUTAGE_SPEED:
            continue
        by[str(r["id"])][t.hour].append(v)
    out = {k: {h: float(statistics.median(vs)) for h, vs in sorted(hours.items()) if any(v != OUTAGE_SPEED for v in vs)}
           for k, hours in by.items()}
    return {k: m for k, m in out.items() if m}


def _runs(hours: list[int]) -> list[tuple[int, int]]:
    """Consecutive hours as [start, end) runs: [7, 8, 9, 12] -> [(7, 10), (12, 13)]."""
    out = []
    for h in hours:
        if out and out[-1][1] == h:
            out[-1] = (out[-1][0], h + 1)
        else:
            out.append((h, h + 1))
    return out


def slow_windows(medians: dict[str, dict[int, float]]) -> list[list[int]]:
    """Union over a group's links: hour h in SLOW_HOURS is slow when the link's median at h is under SLOW_SHARE × that
    link's fastest hourly median (all 24 hours). Consecutive slow hours become [start, end) windows. Hours may be
    text, as JSON gives them."""
    slow = set()
    for m in medians.values():
        m = {int(h): v for h, v in m.items()}
        if not m:
            continue
        top = max(m.values())
        slow |= {h for h in range(*SLOW_HOURS) if h in m and m[h] < SLOW_SHARE * top}
    return [[a, b] for a, b in _runs(sorted(slow))]


def decode_polyline(text: str) -> list[list[float]]:
    """Google's encoded polyline -> [[lon, lat], ...]; a text cut short ends at its last whole point."""
    out, i, lat, lon = [], 0, 0, 0
    try:
        while i < len(text):
            vals = []
            for _ in range(2):
                shift = res = 0
                while True:
                    b = ord(text[i]) - 63
                    i += 1
                    res |= (b & 0x1F) << shift
                    shift += 5
                    if b < 0x20:
                        break
                vals.append(~(res >> 1) if res & 1 else res >> 1)
            lat, lon = lat + vals[0], lon + vals[1]
            out.append([lon / POLY_PRECISION, lat / POLY_PRECISION])
    except IndexError:
        pass
    return out


def link_points(link: dict) -> list[list[float]]:
    """The [lon, lat] points of a link's `link_points` text ("40.7,-73.9 40.71,-73.95 ..."); unreadable pairs are skipped
    (the feed cuts these texts short, and some pairs are garbled)."""
    pts = []
    for pair in str(link.get("link_points") or "").split():
        try:
            lat, lon = (float(x) for x in pair.split(","))
        except ValueError:
            continue
        pts.append([lon, lat])
    return pts


def _in_box(pts: list, box) -> list:
    if not box:
        return pts
    w, s, e, n = box
    return [p for p in pts if w <= p[0] <= e and s <= p[1] <= n]


def merge_links(rows: list[dict], box=None) -> list[dict]:
    """One {id, name, line} per link id, line as [[lon, lat], ...] inside box [west, south, east, north] when given.
    The line is the link's encoded_poly_line, the shortest text when there are several (the others repeat its
    backslashes, checked on 24 Nov), else its link_points (which the feed cuts short)."""
    by = defaultdict(list)
    for r in rows:
        by[str(r.get("id"))].append(r)
    out = []
    for lid, rs in sorted(by.items()):
        polys = sorted({r["encoded_poly_line"] for r in rs if r.get("encoded_poly_line")}, key=len)
        line = _in_box(decode_polyline(polys[0]), box) if polys else []
        if len(line) < 2:
            line = max((_in_box(link_points(r), box) for r in rs), key=len, default=[])
        out.append({"id": lid, "name": rs[0].get("link_name", ""), "line": line})
    return out


def _samples(line: list) -> list[list[float]]:
    """Points along a [lon, lat] line, SPEED_SAMPLE_M apart, the vertices included."""
    out = []
    for a, b in zip(line, line[1:]):
        n = max(1, math.ceil(haversine_m(a[1], a[0], b[1], b[0]) / SPEED_SAMPLE_M))
        out += [[a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n] for k in range(n)]
    return out + line[-1:]


def _cell(lon: float, lat: float) -> tuple[int, int]:
    return math.floor(lon / CELL_DEG), math.floor(lat / CELL_DEG)


def join_shares(link: dict, index: dict, within_m: float = SPEED_JOIN_M) -> dict[str, float]:
    """Group key -> the share of the link's points (see _samples) that lie within within_m of one of the group's pieces."""
    pts = _samples(link["line"])
    on = defaultdict(int)
    for lon, lat in pts:
        cx, cy = _cell(lon, lat)
        to_xy = local_xy(lat, lon)
        near = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for g, c in index.get((cx + dx, cy + dy), ()):
                    if g in near:
                        continue
                    if min(seg_distance((0.0, 0.0), to_xy(*a[:2]), to_xy(*b[:2]))[0] for a, b in zip(c, c[1:])) <= within_m:
                        near.add(g)
        for g in near:
            on[g] += 1
    return {g: n / len(pts) for g, n in on.items()} if pts else {}


def piece_index(roads: dict, features: list) -> dict:
    """The pieces in groups (ramps left out), by CELL_DEG cell: {(x, y): [(group key, coordinates), ...]}."""
    index = defaultdict(list)
    for f in features:
        p = f["properties"]
        if p.get("g") not in roads or p.get("l"):
            continue
        c = f["geometry"]["coordinates"]
        xs, ys = [x[0] for x in c], [x[1] for x in c]
        (x0, y0), (x1, y1) = _cell(min(xs), min(ys)), _cell(max(xs), max(ys))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                index[(x, y)].append((p["g"], c))
    return index


def join_links(links: list[dict], roads: dict, features: list, within_m: float = SPEED_JOIN_M) -> dict[str, str]:
    """Link id -> group key: the road the link runs along, the group with the largest share of the link's points
    within within_m of its pieces, when that share is SPEED_ALONG_SHARE or more. A link with no line joins nothing."""
    index = piece_index(roads, features)
    out = {}
    for link in links:
        if len(link.get("line") or ()) < 2:
            continue
        shares = join_shares(link, index, within_m)
        if shares:   # a tie (a road over another, like the FDR over South Street) goes to the higher class
            g = max(shares, key=lambda k: (shares[k], -CLASS_ORDER.get(roads[k].get("class"), len(CLASS_ORDER)), k))
            if shares[g] >= SPEED_ALONG_SHARE:
                out[str(link["id"])] = g
    return out


def group_slow(joined: dict[str, str], medians: dict[str, dict[int, float]]) -> dict[str, list[list[int]]]:
    """Group key -> its whole-road slow windows, from the medians of the links joined to it; groups with no slow hour
    are left out."""
    by_group = defaultdict(dict)
    for link, g in joined.items():
        if link in medians:
            by_group[g][link] = medians[link]
    out = {}
    for g, m in by_group.items():
        w = slow_windows(m)
        if w:
            out[g] = w
    return out


def group_links(joined: dict[str, str], medians: dict[str, dict[int, float]], lines: dict[str, list]) -> dict[str, list[dict]]:
    """Group key -> [{id, slow, line}] for its joined links that have readings: each link's own slow windows (maybe
    none) and its line, so setup can take the stretch near the home."""
    out = defaultdict(list)
    for link, g in sorted(joined.items()):
        if link in medians:
            out[g].append({"id": link, "slow": slow_windows({link: medians[link]}), "line": lines[link]})
    return dict(out)
