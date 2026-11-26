#!/usr/bin/env python3
"""Freeze small US test geometry after grouping each complete Counted metro.

Usage: python3 scripts/roads/freeze_us_clip.py <extract.osm.pbf>
       --points tests/fixtures/roads/us-points.json
       --out tests/fixtures/roads/us-clip.json.gz [--clip-m M] [--work DIR]

Only osmium is external. No counts, speeds, network requests or release checks
run here; check_us.preflight checks the frozen picks separately. Work products
are kept under --work, or removed when the default temporary directory closes.
"""
import argparse
import gzip
import json
import math
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import us_geometry as ug  # noqa: E402
from roads import FIND_M  # noqa: E402 (build adds the app root)

EXTRACT_PAD_M = 5000  # metres: include nearby nodes of ways that cross the data box without a node inside it
CLIP_MARGIN_M = 2000  # metres: keep 2 km of geometry context beyond the picker's 1 km search, like US data margins
DEFAULT_CLIP_M = FIND_M + CLIP_MARGIN_M  # metres: 3 km; whole qualifying pieces keep their complete geometry


def extract_export(pbf: Path, region: dict, work: Path) -> Path:
    """Extract complete ways around the metro, then use the US build's filter/export.

    The pad helps complete_ways select crossing ways with nodes just outside
    the data box. The caller clips back to the exact box before grouping.
    """
    cut = work / "region.osm.pbf"
    box = ug.pad_box(region["data_box"], EXTRACT_PAD_M)
    build._osmium("extract", str(pbf), "-b", ",".join(str(v) for v in box),
                  "-s", "complete_ways", "-o", str(cut), "--overwrite", timeout_s=build.US_OSMIUM_TIMEOUT_S)
    return build.run_osmium(cut, work, timeout_s=build.US_OSMIUM_TIMEOUT_S)


def freeze(pbf: Path, points: Path, out: Path, work: Path, clip_m: float = DEFAULT_CLIP_M) -> int:
    """Write one deterministic RoadFile document; return its compressed bytes."""
    if not math.isfinite(clip_m) or clip_m <= 0:
        raise ValueError("--clip-m must be a positive finite distance in metres")
    by_cbsa = defaultdict(list)
    for p in json.loads(Path(points).read_text(encoding="utf-8"))["points"]:
        lat, lon = p["lat"], p["lon"]
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"Invalid point in CBSA {p['cbsa']}")
        by_cbsa[p["cbsa"]].append((lat, lon))
    if not by_cbsa:
        raise ValueError("The points file has no points")
    metros, _ = build.load_us_regions()
    selected = [m for m in metros if m["cbsa"] in by_cbsa]
    missing = set(by_cbsa) - {m["cbsa"] for m in selected}
    if missing:
        raise ValueError(f"No frozen Counted metro for CBSA {', '.join(sorted(missing))}")
    # With no states this returns only the selected metro regions. Their data
    # boxes are identical to build_us; unrelated nationwide squares are omitted.
    regions = build.us_regions(selected, {})
    osm_date = build.osm_date_of(Path(pbf))
    if not osm_date:
        raise ValueError("Input header has no OSM date")
    # An extract's date, rather than today's date, keeps reruns stable across days.
    built = date.fromisoformat(osm_date)
    doc = {"type": "FeatureCollection", "metro": "us-clip", "country": "US", "level": build.US_LEVEL,
           "osm_date": osm_date, "built": built.isoformat(), "licence": build.LICENCE,
           "credit": build.CREDIT, "source": build.SOURCE_URL, "roads": {}, "features": []}
    for region in regions:
        folder = Path(work) / region["id"]
        folder.mkdir(parents=True, exist_ok=True)
        source = extract_export(Path(pbf), region, folder)
        features = build.clip_to_box(build.read_features(source), region["data_box"])
        roads, features = build.region_roads(region, features)
        features = build.clip_features(features, by_cbsa[region["cbsa"]], clip_m)
        keys = {f["properties"]["g"] for f in features if f["properties"].get("g") is not None}
        roads = {f"{region['id']}:{key}": road for key, road in roads.items() if key in keys}
        for f in features:
            key = f["properties"].get("g")
            if key is not None:
                f["properties"]["g"] = f"{region['id']}:{key}"
        entry = build.write_region(region, roads, features, folder, built, osm_date)
        with gzip.open(folder / entry["file"], "rt", encoding="utf-8") as stream:
            part = json.load(stream)
        doc["roads"].update(part["roads"])
        doc["features"].extend(part["features"])
        print(f"{region['id']}: {len(features)} pieces kept, {len(roads)} roads kept, {entry['bytes']} bytes", flush=True)
    data = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                         compresslevel=build.GZIP_LEVEL, mtime=0)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{out}: {len(data)} bytes", flush=True)
    return len(data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("extract", type=Path)
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--clip-m", type=float, default=DEFAULT_CLIP_M)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args(argv)
    if args.work is None:
        with tempfile.TemporaryDirectory(prefix="freeze-us-clip-") as work:
            freeze(args.extract, args.points, args.out, Path(work), args.clip_m)
    else:
        freeze(args.extract, args.points, args.out, args.work, args.clip_m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
