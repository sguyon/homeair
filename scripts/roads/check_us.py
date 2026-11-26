"""Offline Level 2 release checks: hand-reviewed picks and area-based sanity reports.

The fixed expectations come from maps, never from the picking code. A release
cannot pass while those fixtures are missing, incomplete or marked placeholder.
"""
import gzip
import hashlib
import json
import math
import random
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roads import REACH_M, RoadFile, find_roads  # noqa: E402

SANITY_N = 1000  # points per metro core: reproducible area samples, not population samples
SANITY_SEED = 20261124  # base seed; numeric CBSA separates each metro's samples
HEI_LOW = 0.30  # inclusive lower share of people near major roads; a warning reference only
HEI_HIGH = 0.45  # inclusive upper share of people near major roads; a warning reference only
FIXED_CBSAS = ("31080", "16980", "19100", "26420", "47900", "37980", "33100", "12060", "14460", "38060")  # the ten spec metros, Census 2025 codes
FIXED_KINDS = ("highway", "between", "suburb", "rural")  # one hand check of each kind per fixture metro
US_METROS = 50  # every frozen Counted metro needs a report
LIMITATION = "HEI measures people; these samples measure area, including water and parks."


def _points(path: Path) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"US release blocked: missing hand-checked points file {path}; choose the 40 points from map evidence.")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("placeholder") is not False:
        raise ValueError(f"US release blocked: {path} is a placeholder; the 40 points need hand-checked map evidence.")
    points = doc.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("US release blocked: the 40 hand-checked points are missing.")
    for p in points:
        try:
            valid = (p["kind"] in FIXED_KINDS and isinstance(p["cbsa"], str) and p["cbsa"].isdigit()
                     and bool(p["label"]) and math.isfinite(p["lat"]) and math.isfinite(p["lon"])
                     and -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180
                     and isinstance(p["expected"], list)
                     and all(isinstance(r, list) and len(r) == 3 and all(isinstance(s, str) for s in r) for r in p["expected"])
                     and isinstance(p["not_followed"], list) and all(isinstance(n, str) for n in p["not_followed"])
                     and bool(p["sources"]) and isinstance(p["sources"], list)
                     and all(isinstance(s, str) and s.startswith(("https://", "http://")) for s in p["sources"]))
            date.fromisoformat(p["reviewed"])
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError(f"Invalid hand-checked US point: {p!r}; include expected picks, roads not followed, sources and review date.")
    return points


def _metros(index: dict) -> dict[str, dict]:
    entries = [m for m in index["metros"] if m.get("kind") == "metro" and m.get("country") == "US"
               and m.get("level") == "counted"]
    metros = {m["cbsa"]: m for m in entries}
    if len(metros) != len(entries):
        raise ValueError("Duplicate CBSA in the US Counted index")
    return metros


def _load(meta: dict, folder: Path) -> RoadFile:
    path = Path(folder) / meta["file"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != meta.get("sha256"):
        raise ValueError(f"US {meta['cbsa']} file digest mismatch: {path.name}")
    return RoadFile.load(path)


def check_fixed(index: dict, folder: Path, points: Path) -> list[dict]:
    """Check ordered followed (name, band, side), other roads and empty rural picks.

    Each point names its Counted CBSA explicitly: NYC's smaller Measured box
    must not accidentally test a different file instead of this fresh US build.
    """
    metros, loaded = _metros(index), {}

    def data_for(p):
        cbsa = p["cbsa"]
        if cbsa not in metros:
            raise ValueError(f"Hand-checked point {p['label']}: Counted CBSA {cbsa} is missing from the build")
        meta = metros[cbsa]
        w, s, e, n = meta["box"]
        if not (w <= p["lon"] <= e and s <= p["lat"] <= n):
            raise ValueError(f"Hand-checked point {p['label']} is outside CBSA {cbsa}'s core box")
        if cbsa not in loaded:
            loaded[cbsa] = _load(meta, folder)
        return loaded[cbsa]

    return _check_picks(_points(points), data_for, "US")


def _check_picks(points: list[dict], data_for, source: str) -> list[dict]:
    """Use identical ordered expectations for fresh files and frozen geometry."""
    results, mismatches = [], []
    for p in points:
        picks = find_roads(p["lat"], p["lon"], data_for(p))
        actual = [[f.name, f.band, f.side] for f in picks if f.followed]
        other = [f.name for f in picks if not f.followed]
        passed = actual == p["expected"] and other == p["not_followed"] and (p["kind"] != "rural" or not picks)
        result = {"cbsa": p["cbsa"], "kind": p["kind"], "label": p["label"], "expected": p["expected"],
                  "actual": actual, "not_followed": other, "passed": passed}
        results.append(result)
        if not passed:
            mismatches.append(f"{p['label']}: expected {p['expected']!r}, actual {actual!r}; "
                              f"roads not followed expected {p['not_followed']!r}, actual {other!r}")
    if mismatches:
        raise ValueError(f"{source} fixed-point mismatch: " + "; ".join(mismatches))
    return results


def _write_report(report: dict, out: Path) -> None:
    reports = Path(out) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"us-{report['cbsa']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sanity_report(meta: dict, data: RoadFile, out: Path) -> dict:
    """Sample uniformly by area in the core box; an out-of-band share only warns."""
    seed = SANITY_SEED + int(meta["cbsa"])
    rng = random.Random(seed)
    w, s, e, n = meta["box"]
    sin_s, sin_n = math.sin(math.radians(s)), math.sin(math.radians(n))
    near_n = 0
    for _ in range(SANITY_N):
        lon = rng.uniform(w, e)
        lat = math.degrees(math.asin(rng.uniform(sin_s, sin_n)))
        # The picker defines tier 1 as a highway within REACH_M, tier 2 as a
        # closer primary. Tier 3's calm-morning roads do not count here.
        if any(f.followed and f.tier in (1, 2) for f in find_roads(lat, lon, data)):
            near_n += 1
    share = near_n / SANITY_N
    report = {"cbsa": meta["cbsa"], "sha256": meta["sha256"], "fixed_checks": [],
              "seed": seed, "sample_n": SANITY_N, "near_n": near_n, "share": share,
              "warning": not HEI_LOW <= share <= HEI_HIGH, "reach_m": REACH_M, "limitation": LIMITATION}
    _write_report(report, out)
    if report["warning"]:
        print(f"Warning: US {meta['cbsa']} near-road area share {share:.1%} is outside {HEI_LOW:.0%}–{HEI_HIGH:.0%}. {LIMITATION}")
    return report


def preflight(points: Path) -> None:
    """Reject unfinished fixtures and wrong frozen picks before national extraction."""
    fixed = _points(points)
    expected = Counter((cbsa, kind) for cbsa in FIXED_CBSAS for kind in FIXED_KINDS)
    if Counter((p["cbsa"], p["kind"]) for p in fixed) != expected:
        raise ValueError("US release blocked: need 40 hand-checked points, one per kind in each of the ten specified metros.")
    clip = Path(points).with_name("us-clip.json.gz")
    if not clip.is_file():
        raise ValueError(f"US release blocked: missing hand-checked frozen clip {clip}")
    with gzip.open(clip, "rt", encoding="utf-8") as f:
        frozen = json.load(f)
    if frozen.get("placeholder") or not frozen.get("roads") or not frozen.get("features"):
        raise ValueError(f"US release blocked: {clip} is a placeholder; freeze the roads around the 40 hand-checked points.")
    data = RoadFile.load(clip)
    _check_picks(fixed, lambda p: data, "US frozen clip")


def check_build(index: dict, folder: Path, points: Path) -> None:
    """Require both frozen and fresh hand checks, then report every Counted metro."""
    preflight(points)
    metros = _metros(index)
    if len(metros) != US_METROS:
        raise ValueError(f"US release blocked: expected {US_METROS} Counted metros, got {len(metros)}")
    results = check_fixed(index, folder, points)
    for cbsa, meta in sorted(metros.items()):
        report = sanity_report(meta, _load(meta, folder), folder)
        report["fixed_checks"] = [r for r in results if r["cbsa"] == cbsa]
        _write_report(report, folder)
