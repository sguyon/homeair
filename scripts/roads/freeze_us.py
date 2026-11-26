#!/usr/bin/env python3
"""Freeze Census 2025 metros and state coverage, before the US road build.

Uses only the standard library. Downloads the named Census inputs to --work
unless local --population, --cbsas and --states ZIP/CSV paths are supplied.
State boundaries are a separate input: CBSA shapes do not cover rural America.
Rings are simplified for clipping; compact full-resolution core ranges preserve
grid coverage exactly, including coastal slivers, holes and small islands.
No download or GIS package is needed at road-build runtime.

python3 scripts/roads/freeze_us.py --work /tmp/roads-us-work
python3 scripts/roads/freeze_us.py --population FILE.csv --cbsas FILE.zip --states FILE.zip
"""
import argparse
import csv
import hashlib
import io
import json
import math
import re
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from us_geometry import (CORE_DEG, area, bounds, clip_ring, geometry_boxes, in_ring,  # noqa: E402
                         land_cells, polygons, state_cells)

HERE = Path(__file__).resolve().parent
VINTAGE = 2025  # Census population date and TIGER/Line boundary vintage
US_METROS = 50  # whole metropolitan statistical areas, ranked by July 1 population
POPULATION_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/metro/totals/cbsa-est2025-alldata.csv"
CBSA_URL = "https://www2.census.gov/geo/tiger/TIGER2025/CBSA/tl_2025_us_cbsa.zip"
STATE_URL = "https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip"
DOWNLOAD_TIMEOUT_S = 120  # one Census request; preparation fails visibly on unavailable sources
SHAPE_HEADER_BYTES = 100  # shapefile format's fixed header
POLYGON_TYPES = (5, 15, 25)  # Polygon, PolygonZ, PolygonM; all store the XY arrays first
DOWNLOAD_CHUNK_B = 1024 * 1024  # bytes: stream large Census ZIPs to disk
SIMPLIFY_DEG = .005  # degrees: Douglas-Peucker error, about 500 m in latitude; cores use full TIGER coverage
SIMPLIFY_PASSES = 10  # halve the tolerance on boundary crossings; fall back to full rings if still needed
MAX_STATE_BYTES = 2_000_000  # bytes: keep frozen state coverage small enough to track in the public repo
STATE_CODES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())


def select_metros(data: bytes) -> list[dict]:
    """Exactly 50 whole MSAs; no micro areas, divisions, counties or Puerto Rico."""
    rows = []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:   # Census's 2025 CSV is Latin-1 ("Cañon City", checked 24 Nov 2026)
        text = data.decode("latin-1")
    for row in csv.DictReader(io.StringIO(text)):
        # the 2025 file has no SUMLEV column (checked 24 Nov 2026): LSAD alone names the metro rows there
        if ("SUMLEV" in row and row["SUMLEV"].strip() != "310") or row.get("LSAD", "").strip() != "Metropolitan Statistical Area":
            continue
        if row.get("MDIV", "").strip(" 0") or row.get("STCOU", "").strip(" 0"):
            continue
        name = row["NAME"].strip()
        match = re.search(r"(?:,|\s-\s)\s*([A-Z]{2}(?:-[A-Z]{2})*)$", name)
        if not match:
            raise ValueError(f"No state codes in Census metro name: {name}")
        states = match[1].split("-")
        if "PR" in states:
            continue
        if not set(states) <= STATE_CODES:
            raise ValueError(f"Unexpected metro states: {name}")
        rows.append({"cbsa": row["CBSA"].strip().zfill(5), "name": name, "states": states,
                     "population": int(row[f"POPESTIMATE{VINTAGE}"].replace(",", ""))})
    rows.sort(key=lambda r: (-r["population"], r["cbsa"]))
    if len(rows) < US_METROS or len({r["cbsa"] for r in rows}) != len(rows):
        raise ValueError("Census population input must contain at least 50 unique whole US metros")
    return [{**row, "rank": rank} for rank, row in enumerate(rows[:US_METROS], 1)]


def metro_regions(metros: list[dict], shapes: dict) -> list[dict]:
    missing = sorted(m["cbsa"] for m in metros if m["cbsa"] not in shapes)
    if missing:
        raise ValueError(f"Selected CBSAs missing from TIGER shapes: {', '.join(missing)}")
    return [{**m, "box": bounds([p for poly in polygons(shapes[m["cbsa"]]) for p in poly[0]])} for m in metros]


def _dbf(data):
    count, header_len, record_len = struct.unpack_from("<IHH", data, 4)
    fields = []
    offset = 32
    while data[offset] != 13:
        field = data[offset:offset + 32]
        fields.append((field[:11].split(b"\0")[0].decode("ascii"), field[16]))
        offset += 32
    rows = []
    for i in range(count):
        record = data[header_len + i * record_len:header_len + (i + 1) * record_len]
        pos, row = 1, {}
        for name, width in fields:
            row[name] = record[pos:pos + width].decode("utf-8").strip()
            pos += width
        rows.append(row if record[:1] != b"*" else None)
    return rows


def _ring_inside(ring, exterior):
    """Test a non-boundary point; TIGER holes can share the first exterior vertex."""
    for a, b in zip(ring, ring[1:]):
        for x, y in (a, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]):
            if any(min(c[0], d[0]) <= x <= max(c[0], d[0]) and
                   min(c[1], d[1]) <= y <= max(c[1], d[1]) and
                   (d[0] - c[0]) * (y - c[1]) == (d[1] - c[1]) * (x - c[0])
                   for c, d in zip(exterior, exterior[1:])):
                continue
            return in_ring([x, y], exterior)
    return False


def _shape_polygons(rings):
    # Nesting, rather than winding or record order, identifies holes and islands.
    # Each ring belongs to its smallest containing ring; even depths are land.
    sizes, boxes = [area(r) for r in rings], [bounds(r) for r in rings]
    parents, depths, parts = {}, {}, {}
    for i in sorted(range(len(rings)), key=lambda i: sizes[i], reverse=True):
        w, s, e, n = boxes[i]
        containers = [j for j in depths if sizes[j] > sizes[i] and
                      boxes[j][0] <= w and boxes[j][1] <= s and
                      boxes[j][2] >= e and boxes[j][3] >= n]
        parent = next((j for j in sorted(containers, key=lambda j: sizes[j])
                       if _ring_inside(rings[i], rings[j])), None)
        parents[i] = parent
        depths[i] = depths[parent] + 1 if parent is not None else 0
        if depths[i] % 2 == 0:
            parts[i] = [rings[i]]
    for i, parent in parents.items():
        if depths[i] % 2:
            parts[parent].append(rings[i])
    return {"type": "MultiPolygon", "coordinates": list(parts.values())}


def read_shapes(path: Path) -> list[tuple[dict, dict]]:
    """Read TIGER geographic Polygon SHP+DBF directly; no GIS runtime dependency."""
    with zipfile.ZipFile(path) as z:
        shp = [n for n in z.namelist() if n.lower().endswith(".shp")]
        dbf = [n for n in z.namelist() if n.lower().endswith(".dbf")]
        if len(shp) != 1 or len(dbf) != 1:
            raise ValueError("Census ZIP must contain exactly one SHP and DBF")
        rows, raw = _dbf(z.read(dbf[0])), z.read(shp[0])
    offset, shapes = SHAPE_HEADER_BYTES, []
    while offset < len(raw):
        _, words = struct.unpack_from(">II", raw, offset)
        record = raw[offset + 8:offset + 8 + words * 2]
        shape_type = struct.unpack_from("<I", record)[0]
        if shape_type == 0:
            shapes.append(None)
        else:
            if shape_type not in POLYGON_TYPES:
                raise ValueError(f"Expected TIGER Polygon, got shape type {shape_type}")
            parts_n, points_n = struct.unpack_from("<II", record, 36)
            starts = list(struct.unpack_from(f"<{parts_n}I", record, 44)) + [points_n]
            start = 44 + parts_n * 4
            points = [list(struct.unpack_from("<dd", record, start + i * 16)) for i in range(points_n)]
            shapes.append(_shape_polygons([points[a:b] for a, b in zip(starts, starts[1:])]))
        offset += 8 + words * 2
    if len(rows) != len(shapes):
        raise ValueError("TIGER SHP and DBF record counts differ")
    return [(row, shape) for row, shape in zip(rows, shapes) if row is not None and shape is not None]


def split_antimeridian(geometry):
    """Unwrap rings first, then cut at ±180; Alaska never has a global envelope."""
    out = []
    for poly in polygons(geometry):
        unwrapped = []
        for ring in poly:
            pts = [list(ring[0])]
            for lon, lat in ring[1:]:
                pts.append([lon + 360 * round((pts[-1][0] - lon) / 360), lat])
            if unwrapped:
                shift = 360 * round((unwrapped[0][0][0] - pts[0][0]) / 360)
                pts = [[x + shift, y] for x, y in pts]
            unwrapped.append(pts)
        w, _, e, _ = bounds(unwrapped[0])
        for shift in range(math.floor((w + 180) / 360), math.floor((e + 180) / 360) + 1):
            box = [-180 + 360 * shift, -90, 180 + 360 * shift, 90]
            rings = [clip_ring(r, box) for r in unwrapped]
            if not rings[0] or not area(rings[0]):
                continue
            out.append([[[round(x - 360 * shift, 10), round(y, 10)] for x, y in ring]
                        for ring in rings if ring and area(ring)])
    return {"type": "MultiPolygon", "coordinates": out}


def _simplify_path(points, tolerance):
    """Iterative Douglas-Peucker: bounded stack even for detailed TIGER shores."""
    keep, pending = {0, len(points) - 1}, [(0, len(points) - 1)]
    while pending:
        start, stop = pending.pop()
        ax, ay = points[start]
        dx, dy = points[stop][0] - ax, points[stop][1] - ay
        length2 = dx * dx + dy * dy
        furthest, distance2 = None, tolerance ** 2
        for k in range(start + 1, stop):
            x, y = points[k][0] - ax, points[k][1] - ay
            t = max(0, min(1, (x * dx + y * dy) / length2)) if length2 else 0
            d2 = (x - t * dx) ** 2 + (y - t * dy) ** 2
            if d2 > distance2:
                furthest, distance2 = k, d2
        if furthest is not None:
            keep.add(furthest)
            pending.extend(((start, furthest), (furthest, stop)))
    return [points[k] for k in sorted(keep)]


def _simplify_ring(ring, tolerance):
    # Two distinct anchors avoid collapsing a closed ring to its shared endpoint.
    anchor = max(range(len(ring) - 1),
                 key=lambda i: (ring[i][0] - ring[0][0]) ** 2 + (ring[i][1] - ring[0][1]) ** 2)
    result = _simplify_path(ring[:anchor + 1], tolerance)[:-1] + _simplify_path(ring[anchor:], tolerance)
    # Keep small islands and holes verbatim when simplification collapses them.
    return result if len(result) >= 4 and area(result) else ring


def _rings_cross(geometry):
    # Sweep longitude envelopes, checking all rings including separate islands.
    edges = sorted((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), a, b)
                   for poly in polygons(geometry) for ring in poly for a, b in zip(ring, ring[1:]))
    active = []

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    for west, east, south, north, a, b in edges:
        active = [e for e in active if e[1] >= west]
        for _, _, low, high, c, d in active:
            if (high >= south and north >= low and cross(a, b, c) * cross(a, b, d) < 0
                    and cross(c, d, a) * cross(c, d, b) < 0):
                return True
        active.append((west, east, south, north, a, b))
    return False


def _simplify_geometry(geometry):
    tolerance = SIMPLIFY_DEG
    for _ in range(SIMPLIFY_PASSES):
        candidate = {"type": "MultiPolygon", "coordinates": [
            [_simplify_ring(r, tolerance) for r in p] for p in polygons(geometry)]}
        if (not _rings_cross(candidate)
                and all(_ring_inside(h, p[0]) for p in polygons(candidate) for h in p[1:])):
            return candidate, tolerance
        tolerance /= 2
    return geometry, 0  # retaining original rings cannot introduce a simplification crossing


def freeze_state(geometry):
    """Simplified rings plus exact full-TIGER cores, packed as [row, start, stop)."""
    full = land_cells(geometry, CORE_DEG)
    rows = []
    for i, j in sorted(full, key=lambda cell: (cell[1], cell[0])):
        if rows and rows[-1][0] == j and rows[-1][2] == i:
            rows[-1][2] = i + 1
        else:
            rows.append([j, i, i + 1])
    simplified, tolerance = _simplify_geometry(geometry)
    state = {"boxes": geometry_boxes(geometry), "geometry": simplified,
             "geometry_tolerance_degrees": tolerance, "core_step": CORE_DEG, "core_rows": rows}
    frozen = state_cells(state, CORE_DEG)
    if not full <= frozen:
        raise ValueError("Simplified state coverage dropped full-TIGER cores")
    state["coverage_check"] = {"full_cores": len(full), "frozen_cores": len(frozen), "full_subset_frozen": True}
    return state


def freeze(population: Path, cbsas: Path, states: Path, out: Path):
    top = select_metros(population.read_bytes())
    chosen = {m["cbsa"] for m in top}
    metro_shapes = {r["GEOID"]: g for r, g in read_shapes(cbsas) if r["GEOID"] in chosen}
    metros = metro_regions(top, metro_shapes)
    coverage = {}
    for row, geom in read_shapes(states):
        code = row["STUSPS"]
        if code in STATE_CODES:
            geom = split_antimeridian(geom)
            coverage[code] = {"name": row["NAME"], "fips": row["STATEFP"], **freeze_state(geom)}
    if set(coverage) != STATE_CODES:
        raise ValueError(f"Missing state coverage: {sorted(STATE_CODES - set(coverage))}")
    sources = {"population": {"url": POPULATION_URL, "sha256": hashlib.sha256(population.read_bytes()).hexdigest()},
               "cbsas": {"url": CBSA_URL, "sha256": hashlib.sha256(cbsas.read_bytes()).hexdigest()},
               "states": {"url": STATE_URL, "sha256": hashlib.sha256(states.read_bytes()).hexdigest()}}
    common = {"vintage": VINTAGE, "population_date": "2025-07-01", "crs": "EPSG:4326",
              "source_crs": "TIGER geographic NAD83 longitude/latitude", "sources": sources}
    out.mkdir(parents=True, exist_ok=True)
    for name, doc in (("us-metros.json", {**common, "metros": metros}),
                      ("us-states.json", {**common, "simplification_degrees": SIMPLIFY_DEG,
                                          "core_rows_format": "[latitude row, first longitude column, exclusive stop]",
                                          "states": coverage})):
        path = out / name
        temporary = path.with_suffix(".tmp")
        encoded = (json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if name == "us-states.json" and len(encoded) > MAX_STATE_BYTES:
            raise ValueError(f"Frozen state coverage exceeds {MAX_STATE_BYTES} bytes: {len(encoded)}")
        temporary.write_bytes(encoded)
        temporary.replace(path)
    return {"metros": len(metros), "states": len(coverage)}


def _download(url, work):
    path = work / url.rsplit("/", 1)[-1]
    if not path.is_file():
        print(f"Downloading {url}", flush=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response, temp.open("wb") as f:
                while chunk := response.read(DOWNLOAD_CHUNK_B):
                    f.write(chunk)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, default=Path("/tmp/roads-us-work"))
    ap.add_argument("--out", type=Path, default=HERE)
    ap.add_argument("--population", type=Path)
    ap.add_argument("--cbsas", type=Path)
    ap.add_argument("--states", type=Path)
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    result = freeze(args.population or _download(POPULATION_URL, args.work),
                    args.cbsas or _download(CBSA_URL, args.work),
                    args.states or _download(STATE_URL, args.work), args.out)
    print(f"Frozen {result['metros']} Census metros and {result['states']} states/DC in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
