#!/usr/bin/env python3
"""Build Home Air's road file for one metro from an OpenStreetMap extract.

What it does: keeps the motorways, trunk roads and primary roads (and their ramps) of an OpenStreetMap
extract with `osmium`, simplifies each piece to 5 metres, groups nearby pieces by name or unnamed route number,
joins the US Federal Highway Administration's traffic counts (HPMS) to the roads of a US metro where matched,
adds weekday slow hours from NYC DOT's speeds where available in the configured NYC box, shows each road under
the name in the metro's reviewed name table, and writes one gzipped GeoJSON file per metro plus `index.json`.

Usage: python3 scripts/roads/build.py <metro> <input.osm.pbf> --out DIR [--osm-date YYYY-MM-DD]
       [--hpms-file HPMS2024.zip | --no-counts] [--no-speeds] [--previous DIR] [--work DIR] [--clip-points FILE --clip-m M]
       python3 scripts/roads/build.py us <us-latest.osm.pbf> --out DIR --work DIR --hpms-file HPMS2024.zip [--timeout SECONDS]
  us            Census metros and remaining US blocks, after freeze_us.py; the caller supplies the single
                Geofabrik US extract. Includes per-state HPMS, no speed profiles. Saves reports/us-build.json.
  <metro>        a table in metros.toml (for example nyc); names/<metro>.toml is its name table, if it exists
  --osm-date     the extract's date; else read from the input's header (needs `osmium`)
  --hpms-file    the pinned BTS NTAD HPMS2024.zip; required when joining US traffic counts
  --no-counts    skip the traffic counts (a US metro gets them from the local HPMS file by default)
  --no-speeds    skip the slow hours (a metro with `speeds` in metros.toml gets them by default; needs the network)
  --previous DIR a folder with the metro's previous file: when the speed feed has no reading in the last 7 days,
                 each road's slow hours are copied from it
  --work DIR     a folder that keeps HPMS count caches between runs (default: a temporary folder)
  --timeout      US only: seconds per osmium filter/export call (default: 10800)
  --clip-points  a text file with one "lat,lon" per line; with --clip-m, keeps only the pieces that have a
                 vertex within M metres of a listed point (small files for tests)
Needs osmium-tool (https://osmcode.org/osmium-tool/) on the PATH, Python 3.11 or newer,
and requirements-dev.txt for the build-only HPMS reader.

Licence: the road files are a derivative database of OpenStreetMap, published under the Open Database
Licence 1.0 (https://opendatacommons.org/licenses/odbl/1-0/), credit "© OpenStreetMap contributors".
The files link to this script. How the roads are used: docs/roads.md.
"""
import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import threading
import time
from collections import defaultdict, OrderedDict
from datetime import date, timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the app's roads.py holds the geometry helpers
from roads import HIGHWAY_CLASSES, haversine_m, local_xy, seg_distance  # noqa: E402
from road_data import ROAD_FILE_MAX_BYTES  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))   # hpms.py sits beside this script
import hpms  # noqa: E402
import speeds  # noqa: E402
import us_geometry as ug  # noqa: E402
import check_us  # noqa: E402

HERE = Path(__file__).resolve().parent
SOURCE_URL = "https://github.com/sguyon/homeair/tree/main/scripts/roads"  # the build script, linked from every file
LICENCE = "ODbL-1.0"
CREDIT = "© OpenStreetMap contributors"
INDEX_VERSION = 1  # of index.json's shape
CLASSES = ("motorway", "trunk", "primary")  # the road classes kept
KEEP_TAGS = ("name", "ref", "highway", "tunnel", "bridge", "lanes")  # the OSM tags read from the export
SIMPLIFY_M = 5.0  # Douglas-Peucker tolerance in metres: a piece moves at most this far from its source
JOIN_M = 125.0  # metres from an end to another piece: span wide medians and short bridge gaps without joining distant streets
ROUND_DECIMALS = 5  # decimals kept in degrees: about 1 m
GZIP_LEVEL = 9  # smallest file for downloads; build time is less important than file size
THE_SUFFIXES = ("Expressway", "Parkway", "Bridge")  # a name ending in one of these takes "the" unless the table says otherwise
DIRECTION_WORDS = ("east", "west", "north", "south", "eastbound", "westbound", "northbound", "southbound", "eb", "wb", "nb", "sb")  # a trailing one is a carriageway, not another road
OSMIUM_HEADER_DATE = "header.option.osmosis_replication_timestamp"  # the extract's date, in a PBF header
OSMIUM_TIMEOUT_S = 600  # one osmium call may run this long
US_OSMIUM_TIMEOUT_S = 3 * 3600  # seconds per national filter/export call: the US extract needs longer than NYC
SPEED_LINE_SIMPLIFY_M = 20  # metres: a speed link's line is kept this simplified (it only tells which links pass near a home)
US_METROS = 50  # top whole Census 2025 MSAs, excluding Puerto Rico
SQUARE_DEG = ug.CORE_DEG  # degrees: frozen land coverage grid, retained when coarsening into blocks
BLOCK_DEG = 2  # degrees: initial regional cores, aligned to the global 2-degree grid
BLOCK_MIN_DEG = 0.5  # degrees: smallest core after recursively quartering a dense block
BLOCK_MAX_B = 4_000_000  # gzipped bytes: under road_data.py's 8 MB cap with room
MARGIN_M = 2000  # metres: road geometry beyond every core for homes at its edges
US_LEVEL = "counted"  # level 2: HPMS counts where matched, default US class hours elsewhere
US_ESTIMATE_GZ_B = 40_000_000  # bytes: NYC extrapolation, an estimate rather than a release limit
US_ESTIMATE_BUILD_S = 1800  # seconds: research estimate, rather than a release limit
US_EXTRACT_URL = "https://download.geofabrik.de/north-america/us-latest.osm.pbf"
US_BUCKET_DEG = 0.1  # degrees: spatial lookup cells; small enough to avoid scanning all US regions per way
US_OPEN_BUCKETS = 64  # files: bound open descriptors while streaming ways and HPMS rows
US_DISK_SAMPLE_S = 1  # seconds: sample actual file sizes during external filter/export calls too
US_POINTS = HERE.parents[1] / "tests/fixtures/roads/us-points.json"  # hand-reviewed Level 2 release checks
LOCAL_NAME_SUFFIXES = ("street", "st", "avenue", "ave", "road", "rd", "boulevard", "blvd", "plaza")  # local street names on numbered highways
STRUCTURAL_NAME_WORDS = ("bridge", "upper level", "lower level", "local roadway", "express roadway", "ramp")  # structural pieces use the highway's route number


# ---- US regions -------------------------------------------------------------------------------------------

def load_us_regions() -> tuple[list[dict], dict]:
    path = HERE / "us-metros.json"
    if not path.is_file():
        raise RuntimeError("US regions are not frozen: run python3 scripts/roads/freeze_us.py first.")
    metros = json.loads(path.read_text(encoding="utf-8"))["metros"]
    states = hpms.load_states()
    if len(metros) != US_METROS or len({m["cbsa"] for m in metros}) != US_METROS:
        raise ValueError("US preparation must freeze exactly 50 unique Census metros")
    if set(states) != set(hpms.US_STATES):
        raise ValueError("US preparation must include all 50 states and DC")
    return metros, states


def us_regions(metros: list[dict], states: dict) -> list[dict]:
    """Census metro envelopes followed by every remaining land-intersecting core.

    Partially metro-covered blocks stay. Frozen fine-grid coverage preserves
    islands and coastal slivers; empty-road blocks are omitted during writing.
    """
    regions = []
    for m in sorted(metros, key=lambda m: (m["rank"], m["cbsa"])):
        box = list(m["box"])
        regions.append({**m, "id": f"us-{m['cbsa']}", "kind": "metro", "country": "US",
                        "level": US_LEVEL, "box": box, "data_box": ug.pad_box(box, MARGIN_M)})
    cores = [m["box"] for m in regions]
    cells = defaultdict(set)
    fine_per_min = round(BLOCK_MIN_DEG / SQUARE_DEG)
    min_per_block = round(BLOCK_DEG / BLOCK_MIN_DEG)
    for code, state in sorted(states.items()):
        for i, j in ug.state_cells(state, SQUARE_DEG):
            cells[i // fine_per_min, j // fine_per_min].add(code)
    blocks = defaultdict(dict)
    for (i, j), codes in sorted(cells.items()):
        blocks[i // min_per_block, j // min_per_block][i, j] = codes
    for (i, j), land in sorted(blocks.items()):
        box = ug.grid_box(i, j, BLOCK_DEG)
        if not ug.covered_by_boxes(box, cores):
            regions.append(_block_region(box, land))
    return regions


def us_coverage(states: dict) -> list[list[float]]:
    """Every 2-degree parent intersecting the frozen 50-state/DC footprint, even without a published file."""
    cells = set()
    fine_per_block = round(BLOCK_DEG / SQUARE_DEG)
    for state in states.values():
        if "core_rows" in state:
            if state["core_step"] != SQUARE_DEG:
                raise ValueError("Frozen state core step differs from the US build grid")
            for j, start, stop in state["core_rows"]:
                cells.update((i, j // fine_per_block)
                             for i in range(start // fine_per_block, (stop - 1) // fine_per_block + 1))
        else:
            cells.update((i // fine_per_block, j // fine_per_block)
                         for i, j in ug.state_cells(state, SQUARE_DEG))
    return [ug.grid_box(i, j, BLOCK_DEG) for i, j in sorted(cells)]


def _block_region(box, land):
    size = box[2] - box[0]
    i, j = round(box[0] / size), round(box[1] / size)
    return {"id": f"us-block-{size:g}-{i}-{j}", "name": f"US block {size:g}° {i}, {j}",
            "kind": "block", "country": "US", "level": US_LEVEL,
            "states": sorted({code for codes in land.values() for code in codes}),
            "box": box, "data_box": ug.pad_box(box, MARGIN_M), "_land": land}


def _block_quarters(meta, metros):
    w, s, e, n = meta["box"]
    half = (e - w) / 2
    for west in (w, w + half):
        for south in (s, s + half):
            box = [west, south, west + half, south + half]
            land = {(i, j): codes for (i, j), codes in meta["_land"].items()
                    if west <= i * BLOCK_MIN_DEG < west + half and south <= j * BLOCK_MIN_DEG < south + half}
            if land and not ug.covered_by_boxes(box, [m["box"] for m in metros]):
                yield _block_region(box, land)


def _write_us_region(meta, raw_features, segments, out, built, osm_date, metros, splits, level=0):
    # Reclip the original bucket for every quarter: never regroup simplified or
    # published parent features, whose tags and naming context have changed.
    features = clip_to_box(raw_features, meta["data_box"]) if meta["kind"] == "block" else raw_features
    roads, features = region_roads(meta, features)
    if meta["kind"] == "block" and not any(
            f["properties"].get("g") and not f["properties"].get("t") and not f["properties"].get("l")
            for f in clip_to_box(features, meta["box"])):
        return
    joined = hpms.join_by_state(roads, features, segments)
    entry = write_region(meta, roads, features, out, built, osm_date)
    if entry["bytes"] > ROAD_FILE_MAX_BYTES:
        raise ValueError(f"{entry['file']} is {entry['bytes']} bytes, exceeding the download cap of {ROAD_FILE_MAX_BYTES} bytes")
    if (meta["kind"] == "block" and entry["bytes"] > BLOCK_MAX_B
            and meta["box"][2] - meta["box"][0] > BLOCK_MIN_DEG):
        (out / entry["file"]).unlink()
        splits[level] += 1
        # Release the parent's grouped copy before processing its quarters.
        del roads, features, joined
        for quarter in _block_quarters(meta, metros):
            yield from _write_us_region(quarter, raw_features, segments, out, built, osm_date, metros, splits, level + 1)
    else:
        yield entry, joined


def clip_to_box(features: list[dict], box: list[float]) -> list[dict]:
    return ug.clip_features(features, box)


class _RegionLookup:
    """Small spatial lookup for data boxes, including their antimeridian aliases."""
    def __init__(self, regions):
        self.cells = defaultdict(list)
        for idx, region in enumerate(regions):
            for box in ug.canonical_boxes(region["data_box"]):
                for i in ug.grid_range(box[0], box[2], US_BUCKET_DEG):
                    for j in ug.grid_range(box[1], box[3], US_BUCKET_DEG):
                        self.cells[i, j].append(idx)

    def candidates(self, lines):
        found = set()
        for line in lines:
            for part in ug.canonical_lines(line):
                for a, b in zip(part, part[1:]):
                    # Include horizontal and vertical segments on cell edges.
                    lo_i, hi_i = sorted((math.floor(a[0] / US_BUCKET_DEG), math.floor(b[0] / US_BUCKET_DEG)))
                    lo_j, hi_j = sorted((math.floor(a[1] / US_BUCKET_DEG), math.floor(b[1] / US_BUCKET_DEG)))
                    for i in range(lo_i, hi_i + 1):
                        for j in range(lo_j, hi_j + 1):
                            found.update(self.cells.get((i, j), ()))
        return found


class _Buckets:
    """Append JSON lines on disk, keeping at most US_OPEN_BUCKETS files open."""
    def __init__(self, folder):
        self.folder, self.handles = folder, OrderedDict()
        self.sources = defaultdict(set)
        folder.mkdir(parents=True, exist_ok=True)

    def path(self, idx, source):
        return self.folder / f"{idx}-{source}.jsonseq"

    def add(self, idx, source, feature):
        self.sources[idx].add(source)
        key = idx, source
        f = self.handles.pop(key, None)
        if f is None:
            if len(self.handles) >= US_OPEN_BUCKETS:
                _, old = self.handles.popitem(last=False)
                old.close()
            f = self.path(idx, source).open("a", encoding="utf-8")
        f.write(json.dumps(feature, separators=(",", ":")) + "\n")
        self.handles[key] = f

    def close(self):
        for f in self.handles.values():
            f.close()
        self.handles.clear()

    def take(self, idx, source):
        path = self.path(idx, source)
        if not path.is_file():
            return []
        rows = list(_json_lines(path))
        path.unlink()
        return rows


def _json_lines(path):
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip("\x1e \t\r\n")
            if line:
                yield json.loads(line)


class _DiskMeter:
    """Observed peak logical bytes for input, work (including cache), and output.

    Samples during osmium calls as well as each build stage. This records actual
    intermediates rather than equating disk with input.
    Files in overlapping roots are counted only once.
    """
    def __init__(self, *paths):
        paths = sorted({Path(p).resolve() for p in paths}, key=lambda p: len(p.parts))
        self.paths = [p for p in paths if not any(q in p.parents for q in paths if q != p)]
        self.peak = 0
        self.last_sample = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def sample(self, force=False):
        if not self.lock.acquire(blocking=False):
            return
        try:
            if not force and time.monotonic() - self.last_sample < US_DISK_SAMPLE_S:
                return
            total = 0
            for root in self.paths:
                for path in root.rglob("*") if root.is_dir() else [root]:
                    try:
                        if path.is_file():
                            total += path.stat().st_size
                    except OSError:  # a region bucket may be removed during a sample
                        pass
            self.peak = max(self.peak, total)
            self.last_sample = time.monotonic()
        finally:
            self.lock.release()

    def _run(self):
        while not self.stop.wait(US_DISK_SAMPLE_S):
            self.sample()

    def __enter__(self):
        self.sample(force=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        self.sample(force=True)


def _state_boxes(state):
    """A small number of HPMS envelopes per state, rather than one per island."""
    boxes = state.get("boxes") or ug.geometry_boxes(state["geometry"])
    groups = [boxes]
    if max(b[2] for b in boxes) - min(b[0] for b in boxes) > ug.HALF_WORLD_DEG:
        groups = [[b for b in boxes if (b[0] + b[2]) / 2 < 0],
                  [b for b in boxes if (b[0] + b[2]) / 2 >= 0]]
    return [[min(b[0] for b in group), min(b[1] for b in group),
             max(b[2] for b in group), max(b[3] for b in group)] for group in groups if group]


def _bucket_osm(source, regions, lookup, buckets):
    for f in _json_lines(source):
        if (f.get("geometry") or {}).get("type") != "LineString":
            continue
        p = f.get("properties") or {}
        f = {"type": "Feature", "properties": {k: p[k] for k in KEEP_TAGS if k in p}, "geometry": f["geometry"]}
        for idx in lookup.candidates([f["geometry"]["coordinates"]]):
            for clipped in clip_to_box([f], regions[idx]["data_box"]):
                buckets.add(idx, "osm", clipped)
    buckets.close()


def build_us(pbf: Path, out: Path, work: Path, built: date, *, hpms_file: Path,
             timeout_s: float = US_OSMIUM_TIMEOUT_S) -> dict:
    """Build Counted US files and a merged index; never download the PBF or fetch speeds.

    The provided extract is filtered once. OSM and HPMS rows are spatially
    bucketed on disk, then each region is grouped, joined, written and discarded.
    State read errors are retained in reports/us-build.json for release review.
    """
    check_us.preflight(US_POINTS)
    hpms_file = hpms.validate_file(hpms_file)
    start = time.monotonic()
    out, work, pbf = Path(out), Path(work), Path(pbf)
    index_path = out / "index.json"
    existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"metros": []}
    # Retain separately built regions (including Measured NYC), then release
    # the legacy national index before preparing or processing any regions.
    existing_entries = [e for e in existing.get("metros", []) if not e.get("id", "").startswith("us-")]
    del existing
    existing_entries.sort(key=lambda e: (e.get("level") not in (1, "measured"), e.get("id", "")))
    metros, states = load_us_regions()
    regions = us_regions(metros, states)
    query_boxes = {code: _state_boxes(state) for code, state in states.items()}
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    osm_date = osm_date_of(pbf)
    if not osm_date:
        raise ValueError("US input header has no OSM date")
    entries, reports, errors = [], {}, []
    splits = defaultdict(int)
    timings = {"input_download": None}  # the supplied input was downloaded outside this command
    with (_DiskMeter(pbf, hpms_file, work, out) as disk,
          tempfile.TemporaryDirectory(prefix="us-build-", dir=work) as tmp,
          tempfile.TemporaryDirectory(prefix=".us-output-", dir=out) as stage):
        temp = Path(tmp)
        staging = Path(stage)  # same filesystem as output, so replacement is atomic
        lookup, buckets = _RegionLookup(regions), _Buckets(temp / "buckets")
        stamp = time.monotonic()
        source = run_osmium(pbf, temp, timeout_s=timeout_s)
        disk.sample(force=True)
        (temp / "roads.pbf").unlink(missing_ok=True)
        timings["filter_export"] = time.monotonic() - stamp
        stamp = time.monotonic()
        try:
            _bucket_osm(source, regions, lookup, buckets)
        finally:
            buckets.close()
        # A caller's source fixture is never deleted; real exports live in temp.
        if temp in source.parents:
            source.unlink()
        disk.sample(force=True)
        timings["osm_buckets"] = time.monotonic() - stamp
        stamp = time.monotonic()
        for code in sorted(states):
            boxes = query_boxes[code]
            try:
                rows = hpms.fetch_state(code, boxes, work, hpms_file)
                report = hpms.fetch_report(code, boxes, work)
                reports[code] = {"source": hpms.HPMS_SOURCE, "year": hpms.HPMS_YEAR,
                                 "pages": report.get("pages", 0), "rows": len(rows), "status": "ok",
                                 "eligible_groups": 0, "joined_groups": 0, "border_groups": 0, "join_rate": None}
                for f in rows:
                    lines = hpms._lines(f.get("geometry"))
                    for idx in lookup.candidates(lines):
                        # Keep the whole count line in its region's bucket: join
                        # uses its geometry, never a bounding-box-only match.
                        if any(ug.clip_line(line, box) for line in lines
                               for box in ug.canonical_boxes(regions[idx]["data_box"])):
                            buckets.add(idx, code, f)
                del rows
            except (RuntimeError, ValueError) as exc:
                saved = hpms.fetch_report(code, boxes, work)
                reports[code] = {"source": hpms.HPMS_SOURCE, "year": hpms.HPMS_YEAR,
                                 "pages": saved.get("pages", 0), "rows": saved.get("rows", 0),
                                 "eligible_groups": 0, "joined_groups": 0, "border_groups": 0, "join_rate": None,
                                 "status": "error", "error": str(exc)}
                errors.append({"state": code, "error": str(exc)})
            finally:
                buckets.close()
            print(f"HPMS {code}: {reports[code]['pages']} box reads, {reports[code]['rows']} rows; {reports[code]['status']}", flush=True)
            disk.sample()
        timings["hpms"] = time.monotonic() - stamp
        stamp = time.monotonic()
        for idx, meta in enumerate(regions):
            raw_features = buckets.take(idx, "osm")
            # Data margins can cross a state border even if the core does
            # not. Read all nearby count buckets and eligible state shapes.
            nearby = {code for code in states if any(ug.overlaps(b, box) for b in query_boxes[code]
                                                    for box in ug.canonical_boxes(meta["data_box"]))}
            nearby.update(buckets.sources[idx] - {"osm"})
            segments = {code: buckets.take(idx, code) for code in sorted(nearby)}
            for entry, joined in _write_us_region(meta, raw_features, segments, staging, built, osm_date, metros, splits):
                for code, rate in joined.items():
                    # An empty statewide query has no count coverage. Keep
                    # its geometry in the join for neighboring border roads.
                    if reports[code]["status"] == "ok" and reports[code]["rows"] == 0:
                        continue
                    for field in ("eligible_groups", "joined_groups", "border_groups"):
                        reports[code][field] += rate[field]
                entries.append(entry)
                disk.sample()
            del raw_features, segments
        timings["output"] = time.monotonic() - stamp
        for code, report in reports.items():
            eligible = report["eligible_groups"]
            report["join_rate"] = report["joined_groups"] / eligible if eligible and report["status"] == "ok" else None
            print(f"HPMS {code}: {report['joined_groups']} of {eligible} groups joined; rate={report['join_rate']}; {report['status']}", flush=True)
        staged_index = write_index(existing_entries + entries, staging, coverage={"US": us_coverage(states)})
        check_us.check_build(json.loads(staged_index.read_text(encoding="utf-8")), staging, US_POINTS)
        # All checks pass before replacing any previous build artifact. Keep
        # each file replacement atomic and install the merged index last.
        for entry in entries:
            (staging / entry["file"]).replace(out / entry["file"])
        (out / "reports").mkdir(exist_ok=True)
        for path in (staging / "reports").glob("us-*.json"):
            path.replace(out / "reports" / path.name)
        staged_index.replace(index_path)
        disk.sample(force=True)
    total_gz = sum(e["bytes"] for e in entries)
    block_sizes = [e["bytes"] for e in entries if e["kind"] == "block"]
    wall = time.monotonic() - start
    report = {"built": built.isoformat(), "osm_date": osm_date, "input": {"url": US_EXTRACT_URL, "bytes": pbf.stat().st_size if pbf.is_file() else 0},
              "hpms": {"source": hpms.HPMS_SOURCE, "year": hpms.HPMS_YEAR,
                       "sha256": hpms.HPMS_FILE_SHA256, "bytes": hpms.HPMS_FILE_BYTES},
              "timings_s": {**timings, "wall": wall}, "input_download": "provided by caller; not measured",
              "peak_disk_bytes": disk.peak, "disk_measurement": "sampled logical bytes: input + work/cache + output",
              "disk_sample_s": US_DISK_SAMPLE_S, "states": reports, "errors": errors,
              "join_unit": "road group instances in regional files, including duplicated margins",
              "border_groups": "groups intersecting multiple frozen state coverages; counted in each eligible state",
              "metro_count": sum(e["kind"] == "metro" for e in entries),
              "block_count": len(block_sizes), "gzip_bytes": total_gz,
              "block_bytes": {"min": min(block_sizes) if block_sizes else None,
                              "median": median(block_sizes) if block_sizes else None,
                              "max": max(block_sizes) if block_sizes else None},
              "splits_by_level": dict(sorted(splits.items())), "index_bytes": index_path.stat().st_size,
              "estimates": {"gzip_bytes": US_ESTIMATE_GZ_B, "build_s": US_ESTIMATE_BUILD_S,
                            "gzip_ratio": total_gz / US_ESTIMATE_GZ_B, "build_ratio": wall / US_ESTIMATE_BUILD_S,
                            "limits": False}}
    (out / "reports").mkdir(exist_ok=True)
    (out / "reports/us-build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return json.loads(index_path.read_text(encoding="utf-8"))


# ---- metros -----------------------------------------------------------------------------------------------

def metro(metro_id: str) -> dict:
    """The metro's entry from metros.toml: {id, name, level, country, box, source_url, hpms_states, speeds}."""
    with open(HERE / "metros.toml", "rb") as f:
        table = tomllib.load(f)
    if metro_id not in table:
        raise ValueError(f"Unknown metro {metro_id!r}; metros.toml has: {', '.join(sorted(table))}")
    m = table[metro_id]
    return {"id": metro_id, "name": m["name"], "level": m["level"], "country": m["country"], "box": list(m["box"]),
            "source_url": m["source_url"], "hpms_states": list(m.get("hpms_states", [])), "speeds": m.get("speeds")}


# ---- osmium -----------------------------------------------------------------------------------------------

def _osmium(*args, timeout_s: float = OSMIUM_TIMEOUT_S) -> subprocess.CompletedProcess:
    if not shutil.which("osmium"):
        raise SystemExit("osmium is not installed. Install osmium-tool (for example `brew install osmium-tool`) and run this again.")
    return subprocess.run(["osmium", *args], check=True, capture_output=True, text=True, timeout=timeout_s)


def run_osmium(pbf: Path, work: Path, timeout_s: float = OSMIUM_TIMEOUT_S) -> Path:
    """Keep the three road classes and their ramps of `pbf` in work/roads.pbf, export them to work/roads.geojsonseq."""
    kept = work / "roads.pbf"
    filters = ",".join(f"{c},{c}_link" for c in CLASSES)
    _osmium("tags-filter", str(pbf), f"w/highway={filters}", "-o", str(kept), "--overwrite", timeout_s=timeout_s)
    out = work / "roads.geojsonseq"
    _osmium("export", str(kept), "-f", "geojsonseq", "--geometry-types=linestring", "-o", str(out), "--overwrite", timeout_s=timeout_s)
    return out


def osm_date_of(pbf: Path) -> str | None:
    """The date (YYYY-MM-DD) in the input's header, or None when it has none."""
    try:
        text = _osmium("fileinfo", "-g", OSMIUM_HEADER_DATE, str(pbf)).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    m = re.match(r"\d{4}-\d{2}-\d{2}", text)
    return m.group(0) if m else None


def read_features(path: Path) -> list[dict]:
    """The LineString features of a GeoJSON sequence file (record separators allowed), with only KEEP_TAGS."""
    feats = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip("\x1e \t\r\n")
        if not line:
            continue
        f = json.loads(line)
        geom = f.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        props = f.get("properties") or {}
        feats.append({"type": "Feature", "properties": {k: props[k] for k in KEEP_TAGS if k in props},
                      "geometry": {"type": "LineString", "coordinates": geom["coordinates"]}})
    return feats


# ---- geometry ---------------------------------------------------------------------------------------------

def simplify(coords: list, tol_m: float = SIMPLIFY_M) -> list:
    """Douglas-Peucker in local metres (the end points stay), then every coordinate rounded to ROUND_DECIMALS."""
    pts = [[c[0], c[1]] for c in coords]
    if len(pts) > 2:
        to_xy = local_xy(pts[0][1], pts[0][0])
        xy = [to_xy(lon, lat) for lon, lat in pts]
        keep = [False] * len(pts)
        keep[0] = keep[-1] = True
        stack = [(0, len(pts) - 1)]
        while stack:
            i, j = stack.pop()
            (x1, y1), (x2, y2) = xy[i], xy[j]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            far, far_i = -1.0, -1
            for k in range(i + 1, j):
                x, y = xy[k]
                d = math.hypot(x - x1, y - y1) if length == 0 else abs(dy * (x - x1) - dx * (y - y1)) / length
                if d > far:
                    far, far_i = d, k
            if far > tol_m:
                keep[far_i] = True
                stack += [(i, far_i), (far_i, j)]
        pts = [p for p, k in zip(pts, keep) if k]
    return [[round(lon, ROUND_DECIMALS), round(lat, ROUND_DECIMALS)] for lon, lat in pts]


def length_m(coords: list) -> float:
    """The length of a line in metres."""
    return sum(haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(coords, coords[1:]))


def clip_features(feats: list[dict], points: list[tuple[float, float]], clip_m: float) -> list[dict]:
    """Only the features with a vertex within clip_m metres of one of the (lat, lon) points."""
    deg = clip_m / 111000 * 1.5 + 1e-6  # a cheap box test before the exact distance (1.5 covers the longitude at these latitudes)
    out = []
    for f in feats:
        near = False
        for lon, lat in f["geometry"]["coordinates"]:
            for plat, plon in points:
                if abs(lat - plat) <= deg and abs(lon - plon) <= deg * 2 and haversine_m(lat, lon, plat, plon) <= clip_m:
                    near = True
                    break
            if near:
                break
        if near:
            out.append(f)
    return out


# ---- names ------------------------------------------------------------------------------------------------

def refs_of(props: dict) -> list[str]:
    """All route refs, normalized and deduplicated in OSM order."""
    refs = []
    for ref in (props.get("ref") or "").split(";"):
        ref = ref.strip()
        match = re.fullmatch(r"(I|US|[A-Z]{2})[\s-]*(\d+[A-Z]?)", ref.upper())
        if match:
            ref = "-".join(match.groups())
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def route_name(props: dict) -> str | None:
    """The general name of a numbered motorway/trunk, else None for the existing fallback."""
    if props.get("highway") not in HIGHWAY_CLASSES:
        return None
    refs = numbered_refs(refs_of(props))
    if not refs:
        return None
    raw_name = props.get("name") or ""
    name = base_name(raw_name)
    words = re.findall(r"[^\W_]+", name.casefold())
    local = bool(words) and words[-1] in LOCAL_NAME_SUFFIXES
    words = " ".join(re.findall(r"[^\W_]+", raw_name.casefold()))
    structural = any(re.search(r"\b" + re.escape(phrase) + r"\b", words) for phrase in STRUCTURAL_NAME_WORDS)
    name_refs = refs_of({"ref": name})
    number_only = len(name_refs) == 1 and name_refs[0] in refs
    return refs[0] if not name or local or structural or number_only else name


def numbered_refs(refs: list[str]) -> list[str]:
    return [r for r in refs if re.fullmatch(r"(?:I|US|[A-Z]{2})-\d+[A-Z]?", r)]


def base_class(highway: str) -> str:
    """"motorway_link" -> "motorway"."""
    return highway[:-5] if highway.endswith("_link") else highway


class NameTable:
    """A metro's reviewed names: which name to show for a piece, and whether it takes "the"."""

    def __init__(self, the_suffixes=THE_SUFFIXES, rename=(), unnamed=(), *, numbered_routes=True):
        self.the_suffixes = tuple(the_suffixes)
        self.rename = list(rename)
        self.unnamed = list(unnamed)
        self.numbered_routes = numbered_routes

    @classmethod
    def load(cls, path) -> "NameTable":
        with open(path, "rb") as f:
            t = tomllib.load(f)
        return cls(t.get("the_suffixes", THE_SUFFIXES), t.get("rename", []), t.get("unnamed", []),
                   numbered_routes=t.get("numbered_routes", True))

    def _split(self, shown: str) -> tuple[str, bool]:
        """A table's or a piece's name -> (name without "the", takes "the")."""
        if shown.lower().startswith("the "):
            return shown[4:], True
        words = shown.split()
        return shown, len(words) > 1 and words[-1] in self.the_suffixes

    def reviewed(self, props: dict) -> tuple[str, bool] | None:
        """An explicit reviewed override, before the general naming rules."""
        name, refs = props.get("name"), refs_of(props)
        if name:
            # an entry with a `ref` is for that road only, so it wins over an entry for the name alone
            base = piece_name(props)
            for e in sorted((e for e in self.rename if e["name"] in (name, base)), key=lambda e: "ref" not in e):
                if "ref" not in e or refs_of({"ref": e["ref"]})[0] in refs:
                    return self._split(e["shown"])
        else:
            for ref in refs:
                for e in self.unnamed:
                    if refs_of({"ref": e["ref"]})[0] == ref:
                        return self._split(e["shown"])
        return None

    def shown(self, props: dict) -> tuple[str, bool] | None:
        """(shown name without "the", takes "the"), with reviewed overrides first."""
        reviewed = self.reviewed(props)
        if reviewed:
            return reviewed
        route = route_name(props) if self.numbered_routes else None
        if route:
            return (route, False) if route in refs_of(props) else self._split(route)
        return self._split(piece_name(props)) if props.get("name") else None


def piece_name(props: dict) -> str:
    highway = base_class(props.get("highway") or "") in HIGHWAY_CLASSES
    return base_name(props.get("name") or "", travel_directions=highway)


def base_name(name: str, *, travel_directions: bool = True) -> str:
    """Remove trailing lane notes and, on highways, trailing carriageway directions."""
    while True:
        short = re.sub(r"\s*\([^()]*\)\s*$", "", name).strip()
        words = short.split()
        if travel_directions and len(words) > 1 and words[-1].lower() in DIRECTION_WORDS:
            short = " ".join(words[:-1])
        if short == name or not short:
            return name
        name = short


def road_key(name: str) -> str:
    """The shown name without "the", lowercased, anything but letters and digits as "-" ("bqe", "queens-boulevard")."""
    return "-".join(re.findall(r"[^\W_]+", name.lower())) or "road"


# ---- groups -----------------------------------------------------------------------------------------------

class _Union:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)   # the lowest index stays the root: groups keep first-appearance order


def _segment_cells(a, b):
    """Grid cells along a segment, not the full rectangle around it."""
    if a[0] > b[0]:
        a, b = b, a
    ax, ay = a[0] / JOIN_M, a[1] / JOIN_M
    bx, by = b[0] / JOIN_M, b[1] / JOIN_M
    first, last = math.floor(ax), math.floor(bx)
    slope = (by - ay) / (bx - ax) if first != last else 0
    entry = ay
    for x in range(first, last + 1):
        exit_y = by if x == last else ay + (x + 1 - ax) * slope
        low, high = sorted((math.floor(entry), math.floor(exit_y)))
        for y in range(low, high + 1):
            yield x, y
        entry = exit_y


def _join_nearby(indices: list[int], features: list[dict], uf: _Union):
    """Join same-key pieces when an end of either is within JOIN_M of the other's line."""
    if len(indices) < 2:
        return
    lon, lat = features[indices[0]]["geometry"]["coordinates"][0]
    to_xy = local_xy(lat, lon)
    segments, ends, grid = [], [], defaultdict(list)
    for i in indices:
        line = [to_xy(*p) for p in features[i]["geometry"]["coordinates"]]
        ends.extend(((i, line[0]), (i, line[-1])))
        for a, b in zip(line, line[1:]):
            segment = len(segments)
            segments.append((i, a, b))
            for cell in _segment_cells(a, b):
                grid[cell].append(segment)
    for i, p in ends:
        cx, cy = math.floor(p[0] / JOIN_M), math.floor(p[1] / JOIN_M)
        # One cell is JOIN_M wide, so only this cell and its eight neighbours can qualify.
        candidates = {segment for x in range(cx - 1, cx + 2) for y in range(cy - 1, cy + 2)
                      for segment in grid.get((x, y), ())}
        for segment in candidates:
            j, a, b = segments[segment]
            if uf.find(i) != uf.find(j) and seg_distance(p, a, b)[0] <= JOIN_M:
                uf.join(i, j)


def group(features: list[dict], names: NameTable) -> dict[str, dict]:
    """The road groups, keyed by road_key. Sets each feature's properties g (the key, None for a ramp or a piece
    with neither a name nor a route number), c (class), and t / l (1 for a tunnel / a ramp).

    Named pieces join nearby pieces of the same normalized name, independent of
    display rules. In automatic grouping, unnamed and route-only pieces join
    nearby pieces with the same first route number, never a named road. A
    reviewed names-table alias may join an unnamed piece to a named road."""
    n = len(features)
    uf = _Union(n)
    shown = {}
    by_key = defaultdict(list)
    for i, f in enumerate(features):
        p = f["properties"]
        hw = p.get("highway", "")
        p["c"] = base_class(hw)
        tunnel = p.get("tunnel")
        if tunnel and tunnel != "no":
            p["t"] = 1
        p["g"] = None
        if hw.endswith("_link"):
            p["l"] = 1
            continue
        refs = refs_of(p)
        raw_name = piece_name(p)
        name_refs = refs_of({"ref": raw_name})
        route_only = len(name_refs) == 1 and name_refs[0] in refs
        # Only automatic grouping keeps unnamed pieces out of named roads. A reviewed
        # names-table alias may put an unnamed piece in a named road's bucket.
        reviewed = names.reviewed(p)
        identity = reviewed or (names._split(raw_name) if raw_name and not route_only else None)
        if identity:
            by_key[("name", identity[0].casefold())].append(i)
            route = route_name(p) if names.numbered_routes and not reviewed else None
            shown[i] = bool(reviewed), (route, False) if route in refs else identity
        elif refs:
            highway_refs = numbered_refs(refs) if hw in HIGHWAY_CLASSES else []
            ref = (highway_refs or refs)[0]
            by_key[("route" if hw in HIGHWAY_CLASSES else "other ref", ref)].append(i)
            shown[i] = False, (ref, False)
    for indices in by_key.values():
        _join_nearby(indices, features, uf)
    members = defaultdict(list)
    for i in shown:
        members[uf.find(i)].append(i)
    roads, last_suffix = {}, {}
    for root in sorted(members):
        idx = members[root]
        by_name, by_class, by_ref = defaultdict(float), defaultdict(float), defaultdict(float)
        for i in idx:
            f = features[i]
            ln = length_m(f["geometry"]["coordinates"])
            by_class[f["properties"]["c"]] += ln
            by_name[shown[i]] += ln
            for r in refs_of(f["properties"]):
                by_ref[r] += ln
        # A reviewed label wins. Otherwise use the label on the most road length.
        _, (name, the) = max(by_name, key=lambda label: (label[0], by_name[label]))
        base = road_key(name)
        key = base
        if key in roads:
            suffix = last_suffix.get(base, 1) + 1
            while (key := f"{base}-{suffix}") in roads:
                suffix += 1
            last_suffix[base] = suffix
        roads[key] = {"name": ("the " if the else "") + name, "the": the,
                      "class": max(by_class.items(), key=lambda kv: kv[1])[0],
                      "refs": [r for r, _ in sorted(by_ref.items(), key=lambda kv: -kv[1])],
                      "aadt": None, "trucks_pct": None}
        for i in idx:
            features[i]["properties"]["g"] = key
    return roads


def region_roads(meta: dict, feats: list[dict]) -> tuple[dict, list[dict]]:
    """Simplify and group one region's pieces using its reviewed names, if any."""
    for f in feats:
        f["geometry"]["coordinates"] = simplify(f["geometry"]["coordinates"])
    feats = [f for f in feats if len(f["geometry"]["coordinates"]) >= 2]
    table_path = HERE / "names" / f"{meta['id']}.toml"
    names = NameTable.load(table_path) if table_path.is_file() else NameTable()
    return group(feats, names), feats


# ---- traffic counts ---------------------------------------------------------------------------------------

def add_counts(meta: dict, roads: dict, features: list, work: Path, hpms_file: Path) -> list[str]:
    """Join HPMS counts to the groups of a US metro; return the join rate per class as lines ("motorway 41 of 52 joined")."""
    if meta["country"] != "US" or not meta["hpms_states"]:
        return []
    segments = []
    for state in meta["hpms_states"]:
        segments.extend(dict(row, hpms_state=state) for row in hpms.fetch(state, meta["box"], work, hpms_file))
    hpms.join(roads, features, segments)
    lines = []
    for c in CLASSES:
        of = [r for r in roads.values() if r["class"] == c]
        if of:
            lines.append(f"{c} {sum(r['aadt'] is not None for r in of)} of {len(of)} joined")
    return lines


# ---- slow hours -------------------------------------------------------------------------------------------

def previous_slow(meta: dict, previous: Path) -> tuple[str, dict[str, dict]] | None:
    """(file name, group key -> {slow, links}) from the newest <metro>-*.json.gz in `previous`, or None without one."""
    files = sorted(Path(previous).glob(f"{meta['id']}-*.json.gz"))
    if not files:
        return None
    with gzip.open(files[-1], "rt", encoding="utf-8") as f:
        doc = json.load(f)
    return files[-1].name, {k: {x: r[x] for x in ("slow", "links") if r.get(x)} for k, r in doc.get("roads", {}).items()
                            if r.get("slow") or r.get("links")}


def add_speeds(meta: dict, roads: dict, features: list, today: date, previous: Path | None = None, get=None) -> list[str]:
    """Set `slow` (the whole road's slow windows) and `links` (each speed link's own windows and line) on the groups of a
    metro whose `speeds` is "nyc-dot", from the last SPEED_WEEKS of weekday readings; with no reading in the last
    SPEED_STALE_DAYS, copy both from the previous file. Returns the lines to print: every link, joined or not, then
    what was written."""
    if meta.get("speeds") != "nyc-dot":
        return []
    kw = {"get": get} if get else {}
    since = today - timedelta(weeks=speeds.SPEED_WEEKS)
    # the links are listed from the last SPEED_STALE_DAYS only: grouping four weeks of the feed times out (24 Nov)
    links = speeds.merge_links(speeds.fetch_links(today - timedelta(days=speeds.SPEED_STALE_DAYS), **kw), meta["box"])
    joined = speeds.join_links(links, roads, features)
    lines = [f"speed link {l['id']} {l['name']!r}: " + (f"joined to {roads[joined[l['id']]]['name']} ({joined[l['id']]})"
             if l["id"] in joined else "not joined") for l in links]
    rows = speeds.fetch(sorted(joined), since, **kw) if joined else []
    last = speeds.last_reading(rows)
    lines.append(f"speeds: {len(links)} links, {len(joined)} joined, {len(rows)} readings, the last {last or 'none'}")
    if last is None or last.date() < today - timedelta(days=speeds.SPEED_STALE_DAYS):
        prev = previous_slow(meta, previous) if previous else None
        if prev is None:
            lines.append(f"speeds: no reading in the last {speeds.SPEED_STALE_DAYS} days and no previous file: no slow hours written")
            return lines
        name, kept = prev
        for k, v in kept.items():
            if k in roads:
                roads[k].update(v)
        lines.append(f"speeds: no reading in the last {speeds.SPEED_STALE_DAYS} days: slow hours copied from {name} "
                     f"({sum(k in roads for k in kept)} roads)")
        return lines
    medians = speeds.hourly_medians(rows)
    for k, w in speeds.group_slow(joined, medians).items():
        roads[k]["slow"] = w
        lines.append(f"slow hours: {roads[k]['name']} ({k}) {w}")
    simple = {l["id"]: simplify(l["line"], SPEED_LINE_SIMPLIFY_M) for l in links if l["id"] in joined}
    for k, ls in speeds.group_links(joined, medians, simple).items():
        roads[k]["links"] = ls
        lines += [f"slow hours: {roads[k]['name']} ({k}) link {x['id']} {x['slow']}" for x in ls]
    return lines


# ---- output -----------------------------------------------------------------------------------------------

def _out_feature(f: dict) -> dict:
    p = f["properties"]
    props = {"g": p.get("g"), "c": p["c"]}
    if p.get("t"):
        props["t"] = 1
    if p.get("l"):
        props["l"] = 1
    return {"type": "Feature", "properties": props, "geometry": f["geometry"]}


def write_metro(meta: dict, roads: dict, features: list, out: Path, built: date, osm_date: str) -> dict:
    """Write <out>/<metro>-<yyyy-mm>.json.gz (the build month) and return the metro's index entry."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    doc = {"type": "FeatureCollection", "metro": meta["id"], "country": meta["country"], "level": meta["level"],
           "osm_date": osm_date, "built": built.isoformat(), "licence": LICENCE, "credit": CREDIT, "source": SOURCE_URL,
           "roads": roads, "features": [_out_feature(f) for f in features]}
    region_fields = {k: meta[k] for k in ("kind", "box", "data_box", "cbsa") if k in meta} if meta.get("kind") else {}
    doc.update(region_fields)
    data = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                         compresslevel=GZIP_LEVEL, mtime=0)
    name = f"{meta['id']}-{built:%Y-%m}.json.gz"
    (out / name).write_bytes(data)
    return {"id": meta["id"], "name": meta["name"], "level": meta["level"], "country": meta["country"],
            "box": meta["box"], "file": name, "osm_date": osm_date, "built": built.isoformat(),
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), **region_fields}


def write_region(meta: dict, roads: dict, features: list, out: Path, built: date, osm_date: str) -> dict:
    """Task 1's writer for a Counted metro or block."""
    counted = {k: {field: value for field, value in road.items() if field not in ("slow", "links")}
               for k, road in roads.items()}
    return write_metro({**meta, "level": US_LEVEL}, counted, features, out, built, osm_date)


def write_index(entries: list[dict], out: Path, *, coverage: dict | None = None) -> Path:
    """Write <out>/index.json for the given metro entries."""
    path = Path(out) / "index.json"
    doc = {"version": INDEX_VERSION, "licence": LICENCE, "credit": CREDIT, "source": SOURCE_URL, "metros": entries}
    if coverage is not None:
        doc["coverage"] = coverage
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


# ---- command line -----------------------------------------------------------------------------------------

def read_points(path: Path) -> list[tuple[float, float]]:
    pts = []
    for line in Path(path).read_text().splitlines():
        parts = re.split(r"[,\s]+", line.strip())
        if len(parts) == 2:
            pts.append((float(parts[0]), float(parts[1])))
    return pts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build one metro's road file from an OpenStreetMap extract.")
    ap.add_argument("metro")
    ap.add_argument("input", type=Path, help="the metro's extract (.osm.pbf)")
    ap.add_argument("--out", type=Path, required=True, help="folder for the metro file and index.json")
    ap.add_argument("--osm-date", help="the extract's date, YYYY-MM-DD (else read from the input's header)")
    ap.add_argument("--no-counts", action="store_true", help="leave the HPMS traffic counts out")
    ap.add_argument("--no-speeds", action="store_true", help="leave the slow hours from the speed feed out")
    ap.add_argument("--previous", type=Path, help="a folder with the metro's previous file, for stale speeds")
    ap.add_argument("--work", type=Path, help="a folder that keeps HPMS count caches between runs")
    ap.add_argument("--hpms-file", type=Path, help="the pinned BTS NTAD HPMS2024.zip; required when joining HPMS counts")
    ap.add_argument("--timeout", type=float, help="US only: seconds per osmium filter/export call (default: 10800)")
    ap.add_argument("--clip-points", type=Path, help="a file with one lat,lon per line")
    ap.add_argument("--clip-m", type=float, help="with --clip-points: keep pieces with a vertex within this many metres")
    args = ap.parse_args(argv)
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        ap.error("--timeout must be a positive finite number of seconds")
    if args.metro == "us":
        if args.no_counts or args.no_speeds or args.previous or args.clip_points or args.clip_m or args.osm_date:
            ap.error("The US Counted build reads the extract date, includes HPMS, and does not accept metro speed/clipping options.")
        if args.hpms_file is None:
            ap.error(f"The US build needs --hpms-file PATH to HPMS2024.zip (2.6 GB). Download once from {hpms.HPMS_DOWNLOAD_PAGE}")
        if args.work is None:
            ap.error("The US build needs --work DIR for its state caches and disk buckets.")
        try:
            hpms.validate_file(args.hpms_file)
        except (OSError, ValueError) as exc:
            ap.error(f"Cannot read HPMS file {args.hpms_file}: {exc}")
        build_us(args.input, args.out, args.work, date.today(),
                 hpms_file=args.hpms_file,
                 timeout_s=args.timeout if args.timeout is not None else US_OSMIUM_TIMEOUT_S)
        return 0
    if args.timeout is not None:
        ap.error("--timeout is only available for the US build")
    if bool(args.clip_points) != bool(args.clip_m):
        ap.error("--clip-points and --clip-m go together")
    try:
        meta = metro(args.metro)
    except ValueError as e:
        ap.error(str(e))
    if not args.no_counts and meta["hpms_states"]:
        if args.hpms_file is None:
            ap.error(f"Joining HPMS counts needs --hpms-file PATH to HPMS2024.zip (2.6 GB). Download once from {hpms.HPMS_DOWNLOAD_PAGE}")
        try:
            hpms.validate_file(args.hpms_file)
        except (OSError, ValueError) as exc:
            ap.error(f"Cannot read HPMS file {args.hpms_file}: {exc}")
    osm_date = args.osm_date or osm_date_of(args.input)
    if not osm_date:
        ap.error("The input's header has no date: pass --osm-date YYYY-MM-DD.")
    with tempfile.TemporaryDirectory() as tmp:
        feats = read_features(run_osmium(args.input, Path(tmp)))
    if args.clip_points:
        feats = clip_features(feats, read_points(args.clip_points), args.clip_m)
    roads, feats = region_roads(meta, feats)
    if not args.no_counts:
        with tempfile.TemporaryDirectory() as tmp:
            for line in add_counts(meta, roads, feats, args.work or Path(tmp), args.hpms_file):
                print(line)
    if not args.no_speeds:
        for line in add_speeds(meta, roads, feats, date.today(), args.previous):
            print(line)
    entry = write_metro(meta, roads, feats, args.out, date.today(), osm_date)
    write_index([entry], args.out)
    print(f"{entry['file']}: {len(feats)} pieces, {len(roads)} roads, {entry['bytes']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
