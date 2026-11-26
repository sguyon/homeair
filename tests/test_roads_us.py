"""US build contracts on synthetic Census geometry, OSM lines and HPMS fixture rows; no network."""
import gzip
import hashlib
import importlib.util
import json
import struct
import weakref
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from roads import RoadFile
from road_data import metro_for
from road_timing import busy_windows
from config import load_config

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("roads_us_build", ROOT / "scripts/roads/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
import hpms  # noqa: E402

BUILT = date(2026, 11, 24)
HPMS_FIXTURE = ROOT / "tests/fixtures/roads/hpms2024.zip"


@pytest.fixture(autouse=True)
def synthetic_hpms_pin(monkeypatch):
    monkeypatch.setattr(hpms, "HPMS_FILE_BYTES", HPMS_FIXTURE.stat().st_size)
    monkeypatch.setattr(hpms, "HPMS_FILE_SHA256", hashlib.sha256(HPMS_FIXTURE.read_bytes()).hexdigest())


@pytest.mark.parametrize("measured,counted", [(1, 2), ("measured", "counted")])
def test_setup_core_selection_priority_edges_and_longitude(measured, counted):
    # Put broad/lower-priority cores first: index order must not pick the file.
    entries = [
        {"id": "block", "kind": "block", "level": counted, "box": [-76, 40, -72, 42]},
        {"id": "nyc-outer", "kind": "metro", "level": counted, "box": [-75, 40, -73, 42]},
        {"id": "overlap-z", "kind": "metro", "level": counted, "box": [-74.5, 40, -73, 42]},
        {"id": "overlap-a", "kind": "metro", "level": counted, "box": [-74.5, 40, -73, 42]},
        {"id": "nyc", "level": measured, "box": [-74.4, 40.4, -73.6, 41]},
        {"id": "block-small-z", "kind": "block", "level": counted, "box": [-76, 40, -75, 41]},
        {"id": "block-small-a", "kind": "block", "level": counted, "box": [-76, 40, -75, 41]},
        {"id": "ak", "kind": "block", "level": counted, "box": [-150, 60, -148, 62]},
        {"id": "hi", "kind": "block", "level": counted, "box": [-158, 20, -156, 22]},
        {"id": "aleutians", "kind": "block", "level": counted, "box": [-180, 50, -178, 52]},
        {"id": "dc", "kind": "metro", "level": counted, "box": [-78, 38, -76, 40]},
        {"id": "legacy-counted", "level": counted, "box": [-81, 30, -80, 32]},
    ]
    for e in entries:
        e["data_box"] = [-180, -90, 180, 90]  # data margins must never select a file
    index = {"metros": entries}
    for lat, lon, want in [
        (40.7, -74, "nyc"), (40.4, -74.4, "nyc"),  # measured, including west/south
        (41, -74, "overlap-a"), (40.7, -73.6, "overlap-a"),  # NYC north/east out
        (41.5, -74.8, "nyc-outer"), (40.2, -74, "overlap-a"),
        (40, -76, "block-small-a"), (41, -76, "block"),  # block south/west in, north out
        (40.5, -75, "nyc-outer"), (42, -74, None), (40.5, -72, None),
        (61, -149, "ak"), (21, -157, "hi"), (51, 180, "aleutians"),
        (51, 540, "aleutians"), (51, -179, "aleutians"), (38.9, -77, "dc"),
        (31, -80.5, "legacy-counted"), (51.5, -0.12, None),
    ]:
        selected = metro_for(index, lat, lon)
        assert (selected["id"] if selected else None) == want, (lat, lon)
        assert metro_for({"metros": list(reversed(entries))}, lat, lon) == selected


def test_counted_roads_use_us_class_defaults():
    roads = load_config({"CONFIG_TOML": '''
[place]
name = "Philadelphia"
lat = 39.95
lon = -75.16
timezone = "America/New_York"
[[roads]]
name = "I-95"
kind = "highway"
country = "US"
[[roads]]
name = "Broad Street"
kind = "main road"
country = "US"
'''}).roads
    highway, primary = roads
    assert highway.slow == primary.slow == ()
    for weekday in (True, False):
        assert busy_windows(highway, weekday) == (((6, 22),), "busy")
        assert busy_windows(primary, weekday) == (((7, 20),), "busy")


@pytest.mark.parametrize("lon,west,lat", [(.1, .1, .5), (180, -180, 51), (-180, -180, 51)])
def test_setup_searches_at_the_same_longitude_as_its_selected_core(tmp_path, monkeypatch, lon, west, lat):
    import road_data
    monkeypatch.delenv("ROAD_DATA_DIR", raising=False)
    feats = [piece("Edge Road", [[west + .001, lat - .01], [west + .001, lat + .01]],
                   ref=None, highway="primary")]
    groups = build.group(feats, build.NameTable())
    out = tmp_path / "out"
    meta = {"id": "edge", "name": "Edge", "kind": "block", "country": "US", "level": "counted",
            "box": [west, lat - .5, west + 2, lat + .5]}
    item = build.write_region(meta, groups, feats, out, BUILT, "2026-11-21")
    build.write_index([item], out)

    def get(url, **kwargs):
        data = (out / url.rsplit("/", 1)[-1]).read_bytes()
        return SimpleNamespace(status_code=200, iter_content=lambda n: [data], close=lambda: None)

    path = tmp_path / "config.toml"
    path.write_text(f'[place]\nname = "Edge home"\nlat = {lat}\nlon = {lon}\ntimezone = "UTC"\n')
    assert metro_for({"metros": [item]}, lat, lon) == item
    assert road_data.setup_roads(path, None, get=get).status == "written"
    cfg = load_config({"CONFIG_FILE": str(path)})
    assert [(r.name, r.side, r.band) for r in cfg.roads] == [("Edge Road", "E", "within 200 m")]


@pytest.mark.parametrize(("highway", "name", "ref", "expected"), [
    ("motorway", "14th Street", "I 78", "I-78"),
    ("trunk", "Main Street", "US 1", "US-1"),
    ("motorway", "Bridge Lower Level", "I 95", "I-95"),
    ("trunk", "Local Roadway", "NJ 139", "NJ-139"),
    ("motorway", "MAIN ST.", "I-78;US 1", "I-78"),
    ("motorway", "Main Avenue (HOV) East", "I 78", "I-78"),
    ("motorway", "Highway Express Roadway Northbound", "I 78", "I-78"),
    ("motorway", "North Ramp", "I 78", "I-78"),
    ("motorway", "Harbor Freeway (Upper Level)", "I 110", "I-110"),
    ("motorway", "I 78", "I 78", "I-78"),
    ("motorway", "Newbridge Freeway", "I 78", "Newbridge Freeway"),
    ("motorway", "Bridgeton Freeway", "I 78", "Bridgeton Freeway"),
    ("motorway", "Road Runner Freeway", "I 78", "Road Runner Freeway"),
    ("motorway", "Broadway", "I 78", "Broadway"),
    ("motorway", "Harbor Freeway", "I 110", "Harbor Freeway"),
    ("motorway", None, "I 78;US-1;NJ 139", "I-78"),
    ("motorway", "Main Road", None, "Main Road"),
    ("primary", "Main Street", "US 1", "Main Street"),
    ("motorway_link", "Main Street", "I 78", "Main Street"),
])
def test_route_number_fallback_and_word_boundaries(highway, name, ref, expected):
    props = {"highway": highway, "name": name, "ref": ref}
    shown, the = build.NameTable().shown(props)
    assert shown == expected and not the
    if highway not in ("motorway", "trunk") or not ref:
        assert build.route_name(props) is None


def test_route_reviewed_override_wins_with_normalized_refs():
    nyc_names = build.NameTable.load(ROOT / "scripts/roads/names/nyc.toml")
    # An optional reviewed override must also win with the national fallback enabled.
    for names in (nyc_names, build.NameTable(rename=nyc_names.rename)):
        for ref in ("I 78", "I-78"):
            assert names.shown({"highway": "motorway", "name": "14th Street", "ref": ref}) == (
                "New Jersey Turnpike (Newark Bay Extension)", True)
    assert build.NameTable().shown({"highway": "motorway"}) is None


def test_route_display_rules_do_not_merge_different_named_groups():
    coords = [[0, 0], [.001, 0]]
    feats = [piece("Harbor Freeway", coords, "I 110"),
             piece("Main Street", coords, "I-110;US 1"),
             piece("Coastal Freeway", coords, "US-1"),
             piece("Other Road", coords, "I-110"),
             piece("I 110", coords, "I-110")]
    out = build.group(feats, build.NameTable())
    assert out["i-110"]["refs"] == ["I-110", "US-1"]
    assert [f["properties"]["g"] for f in feats] == [
        "harbor-freeway", "i-110", "coastal-freeway", "i-110-2", "i-110-3"]
    assert list(out) == ["harbor-freeway", "i-110", "coastal-freeway", "i-110-2", "i-110-3"]
    names = build.NameTable(rename=[{"name": "Harbor Freeway", "shown": "the Reviewed Highway"}])
    assert list(build.group(feats, names).values())[0]["name"] == "the Reviewed Highway"
    named = [piece("Short Freeway", coords, "I 5"),
             piece("Long Freeway", [[0, 0], [.01, 0]], "I-5")]
    assert [r["name"] for r in build.group(named, build.NameTable()).values()] == ["Short Freeway", "Long Freeway"]


def test_route_only_pieces_use_the_first_number_even_beside_named_roads():
    coords = [[0, 0], [.001, 0]]
    feats = [piece("I 5", coords, "I-5;US 1"),
             piece("First Freeway", coords, "I-5"),
             piece("Second Freeway", coords, "US-1"),
             piece("US 1", coords, "US-1;I 5"),
             piece("First Freeway East", coords, "I-5"),
             piece("US 1", coords, "I-5;US-1")]
    out = build.group(feats, build.NameTable())
    assert [f["properties"]["g"] for f in feats] == [
        "i-5", "first-freeway", "second-freeway", "us-1", "first-freeway", "i-5"]
    assert [r["name"] for r in out.values()] == ["I-5", "First Freeway", "Second Freeway", "US-1"]


def test_same_named_streets_far_apart_are_separate_roads():
    feats = [piece("Washington Street", [[0, lat], [.001, lat]], ref=None, highway="primary")
             for lat in (0, .04)]
    out = build.group(feats, build.NameTable())
    assert list(out) == ["washington-street", "washington-street-2"]
    assert [r["name"] for r in out.values()] == ["Washington Street", "Washington Street"]


def test_divided_road_joins_at_a_carriageway_endpoint_but_not_to_a_distant_namesake():
    # The short carriageway's ends are 111 m from the long one's interior,
    # but over 1 km from its ends. The bridge starts 111 m past the long end.
    feats = [piece("Parkway Drive", coords, ref=None, highway="primary") for coords in (
        [[0, 0], [.04, 0]], [[.01, .001], [.03, .001]],
        [[.041, 0], [.05, 0]], [[0, .04], [.04, .04]],
    )]
    build.group(feats, build.NameTable())
    assert [f["properties"]["g"] for f in feats] == ["parkway-drive"] * 3 + ["parkway-drive-2"]


def test_endpoint_joins_a_segment_interior_but_an_interior_crossing_does_not():
    feats = [piece("Main Road", coords, ref=None, highway="primary") for coords in (
        [[-.01, 0], [.01, 0]], [[0, .0011], [0, .01]],
        [[.005, -.004], [.005, .004]], [[-.005, -.005], [-.005, -.0012]],
    )]
    build.group(feats, build.NameTable())
    # The branch starts 122 m from the main line. The crossing's ends are both
    # out of reach, and the last branch ends 133 m away, just past the cutoff.
    assert [f["properties"]["g"] for f in feats] == ["main-road", "main-road", "main-road-2", "main-road-3"]


def test_segment_distance_checks_grow_with_pieces_not_all_pairs(monkeypatch):
    distance = build.seg_distance
    checks = 0

    def counted(*args):
        nonlocal checks
        checks += 1
        return distance(*args)

    monkeypatch.setattr(build, "seg_distance", counted)
    per_piece = []
    for tiles in (5, 20, 80):
        feats = []
        for tile in range(tiles):
            x, y0 = tile % 10 * .08, tile // 10 * .09
            for row in range(10):
                y = y0 + row * .0027
                # Long, parallel diagonals whose bounding boxes overlap but
                # whose lines are far apart. Each has one staggered partner.
                feats += [piece("County Road", coords, ref=None, highway="primary") for coords in (
                    [[x, y], [x + .045, y + .045]],
                    [[x + .015, y + .0152], [x + .030, y + .0302]],
                )]
        checks = 0
        out = build.group(feats, build.NameTable())
        assert len(out) == len(feats) // 2
        keys = [f["properties"]["g"] for f in feats]
        assert keys[::2] == keys[1::2]
        per_piece.append(checks / len(feats))
    assert 0 < min(per_piece) <= max(per_piece) <= 8, per_piece
    assert max(per_piece) <= 1.25 * min(per_piece), per_piece


@pytest.mark.parametrize("order", [(0, 1, 2, 3), (2, 3, 0, 1)])
def test_split_run_keys_cannot_collide_with_other_road_names(order):
    names = ["County Road A", "County Road A", "County Road A-2", "County Road A-2-2"]
    feats = [piece(names[i], [[0, i * .04], [.001, i * .04]], ref=None, highway="primary") for i in order]
    out = build.group(feats, build.NameTable())
    keys = [f["properties"]["g"] for f in feats]
    assert len(set(keys)) == len(out) == len(feats)
    assert [out[key]["name"] for key in keys] == [names[i] for i in order]
    assert build.group(feats, build.NameTable()) == out
    assert [f["properties"]["g"] for f in feats] == keys


def test_only_a_reviewed_alias_joins_an_unnamed_piece_to_a_named_road():
    feats = [piece("Example Freeway", [[0, 0], [.001, 0]], "I 80"),
             piece(None, [[.001, 0], [.002, 0]], "I 80")]
    assert len(build.group(feats, build.NameTable())) == 2
    names = build.NameTable(unnamed=[{"ref": "I-80", "shown": "Example Freeway"}])
    assert list(build.group(feats, names)) == ["example-freeway"]
    assert [f["properties"]["g"] for f in feats] == ["example-freeway"] * 2


def test_unnamed_motorway_runs_keep_the_number_separate_from_named_roads():
    feats = [piece("Borman Expressway", [[0, .04], [.002, .04]], "I 80"),
             piece(None, [[0, 0], [.001, 0]], "I 80"),
             piece(None, [[.001, 0], [.002, 0]], "I-80"),
             piece(None, [[.002, .04], [.003, .04]], "I 80")]
    out = build.group(feats, build.NameTable())
    assert [f["properties"]["g"] for f in feats] == ["borman-expressway", "i-80", "i-80", "i-80-2"]
    assert [r["name"] for r in out.values()] == ["the Borman Expressway", "I-80", "I-80"]


def test_primary_does_not_inherit_route_number_from_distant_namesake():
    feats = [piece("River Road", [[0, 0], [.001, 0]], None, "primary"),
             piece("River Road", [[0, .04], [.001, .04]], "NJ 29", "trunk")]
    out = build.group(feats, build.NameTable())
    assert out["river-road"]["name"] == "River Road"
    assert out["river-road"]["refs"] == []
    assert out["nj-29"]["name"] == "NJ-29"


def test_short_numbered_trunk_section_does_not_rename_a_connected_avenue():
    feats = [piece("Commonwealth Avenue", [[0, 0], [.01, 0]], "MA 30", "primary"),
             piece("Commonwealth Avenue", [[.01, 0], [.011, 0]], "US 20", "trunk")]
    out = build.group(feats, build.NameTable())
    assert [r["name"] for r in out.values()] == ["Commonwealth Avenue"]


@pytest.mark.parametrize(("highway", "name", "ref", "shown"), [
    ("primary", "North Western Avenue", None, "North Western Avenue"),
    ("primary", "Memorial Drive Southeast", None, "Memorial Drive Southeast"),
    ("primary", "Example Avenue North", None, "Example Avenue North"),
    ("motorway", "I-95 North", "I 95", "I-95"),
    ("trunk", "Example Highway Northbound", None, "Example Highway"),
])
def test_directions_are_removed_only_when_they_label_highway_carriageways(highway, name, ref, shown):
    assert build.NameTable().shown({"highway": highway, "name": name, "ref": ref}) == (shown, False)


def synthetic_checks(tmp_path):
    """Invented test geometry only; never the hand-reviewed release fixtures."""
    import check_us
    folder = tmp_path / "output"
    folder.mkdir()
    feats = [piece("I-9", [[-.01, 0], [.01, 0]]),
             piece("Test Avenue", [[.0015, -.001], [.0015, .002]], ref="", highway="primary")]
    groups = build.group(feats, build.NameTable())
    entries = []
    # Fifty synthetic core boxes exercise the national report loop, including
    # the ten CBSAs the real hand checks must cover. No real places are asserted.
    codes = list(check_us.FIXED_CBSAS) + [str(90000 + n) for n in range(40)]
    for code in codes:
        meta = {"id": f"us-{code}", "cbsa": code, "name": "Synthetic metro",
                "kind": "metro", "level": "counted", "country": "US", "box": [-.02, -.02, .02, .02]}
        entries.append(build.write_region(meta, groups, feats, folder, BUILT, "2026-11-21"))
    points = []
    for code in check_us.FIXED_CBSAS:
        for kind in ("highway", "between", "suburb", "rural"):
            points.append({"cbsa": code, "kind": kind, "label": f"Synthetic {code} {kind}",
                           "lat": .015 if kind == "rural" else .001, "lon": 0,
                           "expected": [] if kind == "rural" else [["I-9", "within 500 m", "S"],
                                                                     ["Test Avenue", "within 200 m", "E"]],
                           "not_followed": [], "sources": ["https://example.org/synthetic-map"],
                           "reviewed": "2026-11-24"})
    path = tmp_path / "us-points.json"
    path.write_text(json.dumps({"placeholder": False, "points": points}))
    (tmp_path / "us-clip.json.gz").write_bytes((folder / entries[0]["file"]).read_bytes())
    return {"metros": entries}, folder, path


@pytest.mark.parametrize("damage", ["name", "band", "side", "order", "not_followed", "rural"])
def test_fixed_checks_assert_order_band_side_and_rural_empty(tmp_path, damage):
    import check_us
    index, folder, points = synthetic_checks(tmp_path)
    results = check_us.check_fixed(index, folder, points)
    assert len(results) == 40 and all(r["passed"] for r in results)
    doc = json.loads(points.read_text())
    if damage in ("name", "band", "side"):
        doc["points"][0]["expected"][0][{"name": 0, "band": 1, "side": 2}[damage]] = "wrong"
    elif damage == "order":
        doc["points"][0]["expected"].reverse()
    elif damage == "not_followed":
        doc["points"][0]["not_followed"] = ["Extra road"]
    else:
        # A listed but unfollowed primary within 1 km also invalidates rural.
        doc["points"][3]["lat"] = .008
        doc["points"][3]["expected"] = [["I-9", "500 m to 1 km", "S"]]
        doc["points"][3]["not_followed"] = ["Test Avenue"]
    points.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="Synthetic.*expected.*actual"):
        check_us.check_fixed(index, folder, points)


@pytest.mark.parametrize(("near_n", "warning"), [(290, True), (300, False), (450, False), (460, True)])
def test_sanity_share_is_reproducible_area_uniform_and_only_warns(tmp_path, monkeypatch, near_n, warning):
    import math
    import random
    import check_us
    index, folder, points = synthetic_checks(tmp_path)
    meta = {**index["metros"][0], "box": [-10, 30, 10, 60]}
    data = RoadFile.load(folder / meta["file"])
    seen = []

    def picks(lat, lon, roads):
        seen.append((lat, lon))
        # Count a point once with two nearby roads. A far followed highway and
        # an unfollowed near road never qualify for the near-road share.
        near = len(seen) <= near_n
        return [SimpleNamespace(followed=near, tier=1), SimpleNamespace(followed=near, tier=2),
                SimpleNamespace(followed=True, tier=3), SimpleNamespace(followed=False, tier=1)]

    monkeypatch.setattr(check_us, "find_roads", picks)
    report = check_us.sanity_report(meta, data, folder)
    assert report["sample_n"] == 1000 and report["seed"] == 20261124 + int(meta["cbsa"])
    assert report["near_n"] == near_n and report["share"] == near_n / 1000
    assert report["warning"] is warning
    assert "people" in report["limitation"] and "area" in report["limitation"]
    assert "water" in report["limitation"] and "parks" in report["limitation"]
    rng = random.Random(20261124 + int(meta["cbsa"]))
    lon = rng.uniform(-10, 10)
    lat = math.degrees(math.asin(rng.uniform(math.sin(math.radians(30)), math.sin(math.radians(60)))))
    assert seen[0] == (lat, lon)
    path = folder / f"reports/us-{meta['cbsa']}.json"
    first = path.read_bytes()
    seen.clear()
    assert check_us.sanity_report(meta, data, folder) == report
    assert path.read_bytes() == first


def test_check_build_writes_all_reports_and_fixed_failures_stop_it(tmp_path):
    import check_us
    index, folder, points = synthetic_checks(tmp_path)
    check_us.check_build(index, folder, points)
    reports = sorted((folder / "reports").glob("us-*.json"))
    assert len(reports) == 50
    by_cbsa = {m["cbsa"]: m for m in index["metros"]}
    for path in reports:
        report = json.loads(path.read_text())
        assert report["sha256"] == by_cbsa[report["cbsa"]]["sha256"]
        assert len(report["fixed_checks"]) == (4 if report["cbsa"] in check_us.FIXED_CBSAS else 0)
    doc = json.loads(points.read_text())
    doc["points"][0]["expected"] = []
    points.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="fixed.*mismatch"):
        check_us.check_build(index, folder, points)


@pytest.mark.parametrize("damage", ["unrelated", "wrong-name", "rural"])
def test_release_checks_verify_pinned_picks_on_the_frozen_clip(tmp_path, damage):
    import check_us
    index, folder, points = synthetic_checks(tmp_path)
    clip = points.with_name("us-clip.json.gz")
    frozen = json.loads(gzip.decompress(clip.read_bytes()))
    if damage == "unrelated":
        for f in frozen["features"]:
            f["geometry"]["coordinates"] = [[x + 1, y + 1] for x, y in f["geometry"]["coordinates"]]
    elif damage == "wrong-name":
        frozen["roads"]["i-9"]["name"] = "Wrong route"
    else:
        # Fresh output still has no picks at the rural points; the clip does.
        frozen["features"].append({**frozen["features"][0], "geometry": {
            "type": "LineString", "coordinates": [[-.01, .015], [.01, .015]]}})
    clip.write_bytes(gzip.compress(json.dumps(frozen).encode()))
    assert all(r["passed"] for r in check_us.check_fixed(index, folder, points))
    with pytest.raises(ValueError, match="clip.*mismatch"):
        check_us.check_build(index, folder, points)


@pytest.mark.parametrize("invalid", ["missing", "placeholder", "empty", "incomplete", "clip-placeholder", "clip-missing"])
def test_release_checks_require_real_complete_fixtures(tmp_path, invalid):
    import check_us
    index, folder, points = synthetic_checks(tmp_path)
    doc = json.loads(points.read_text())
    if invalid == "missing":
        points.unlink()
    elif invalid == "placeholder":
        doc["placeholder"] = True
    elif invalid == "empty":
        doc["points"] = []
    elif invalid == "incomplete":
        doc["points"].pop()
    elif invalid == "clip-placeholder":
        points.with_name("us-clip.json.gz").write_bytes(gzip.compress(b'{"placeholder":true}'))
    else:
        points.with_name("us-clip.json.gz").unlink()
    if points.exists():
        points.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="hand.checked|placeholder|40|clip"):
        check_us.check_build(index, folder, points)


@pytest.mark.parametrize("failure", ["mismatch", "points-placeholder", "clip-placeholder"])
def test_build_us_runs_real_release_gate_before_replacing_any_output(tmp_path, monkeypatch, failure):
    index, folder, points = synthetic_checks(tmp_path)
    states = {"NY": state([-.02, -.02, .02, .02])}
    metros = [{"cbsa": m["cbsa"], "name": m["name"], "rank": rank, "population": 100,
               "states": ["NY"], "box": m["box"]} for rank, m in enumerate(index["metros"], 1)]
    monkeypatch.setattr(build, "US_POINTS", points)
    monkeypatch.setattr(build, "load_us_regions", lambda: (metros, states))
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    monkeypatch.setattr(hpms, "fetch_state", lambda *args: [])
    monkeypatch.setattr(build, "osm_date_of", lambda pbf: "2026-11-21")
    source = tmp_path / "synthetic.geojsonseq"
    feats = [piece("I-9", [[-.01, 0], [.01, 0]]),
             piece("Test Avenue", [[.0015, -.001], [.0015, .002]], ref="", highway="primary")]
    source.write_text("\n".join(json.dumps(f) for f in feats))
    monkeypatch.setattr(build, "run_osmium", lambda *args, **kw: source)
    result = build.build_us(tmp_path / "synthetic.pbf", folder, tmp_path / "work", BUILT, hpms_file=HPMS_FIXTURE)
    assert sum(m["kind"] == "metro" for m in result["metros"]) == 50
    assert len(list((folder / "reports").glob("us-*.json"))) == 51
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    if failure == "mismatch":
        # Move the synthetic highway north, reversing its expected side.
        feats[0]["geometry"]["coordinates"] = [[-.01, .005], [.01, .005]]
        source.write_text("\n".join(json.dumps(f) for f in feats))
    else:
        if failure == "points-placeholder":
            placeholder = tmp_path / "placeholder" / "us-points.json"
            placeholder.parent.mkdir()
            placeholder.write_text(json.dumps({"placeholder": True, "points": []}))
            monkeypatch.setattr(build, "US_POINTS", placeholder)
        else:
            points.with_name("us-clip.json.gz").write_bytes(gzip.compress(b'{"placeholder":true}'))
        monkeypatch.setattr(build, "run_osmium", lambda *a, **kw: pytest.fail("extraction ran before placeholder preflight"))
    with pytest.raises(ValueError, match="fixed.point mismatch|placeholder"):
        build.build_us(tmp_path / "synthetic.pbf", folder, tmp_path / "work", BUILT, hpms_file=HPMS_FIXTURE)
    after = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert after == before


def polygon(w, s, e, n):
    return {"type": "MultiPolygon", "coordinates": [[[[w, s], [e, s], [e, n], [w, n], [w, s]]]]}


def state(box, name="Test"):
    return {"name": name, "boxes": [box], "geometry": polygon(*box)}


def metro(box=None):
    return {"cbsa": "12345", "name": "Test metro and suburbs", "rank": 1,
            "population": 900000, "states": ["NY", "NJ"], "box": box or [-74.1, 40, -74.025, 40.1]}


def piece(name, coords, ref="I 9", highway="motorway"):
    return {"type": "Feature", "properties": {"name": name, "ref": ref, "highway": highway},
            "geometry": {"type": "LineString", "coordinates": coords}}


def segment(oid, coords=None, count=10000, name="Crossing", ref=9):
    return {"type": "Feature", "properties": {"objectid": oid, "route_name": name, "route_number": ref,
            "route_signing": 2, "aadt": count, "aadt_combination": 100, "aadt_single_unit": 100, "f_system": 1},
            "geometry": {"type": "LineString", "coordinates": coords or [[-74, 40], [-73.99, 40]]}}


def test_us_regions_keep_suburbs_margins_and_grid_aligned_blocks():
    states = {"NY": state([-74.1, 40, -73.95, 40.1])}
    regions = build.us_regions([metro()], states)
    m = next(r for r in regions if r["kind"] == "metro")
    assert m["box"] == [-74.1, 40, -74.025, 40.1]  # county envelope, including suburbs
    blocks = [r for r in regions if r["kind"] == "block"]
    assert [r["box"] for r in blocks] == [[-76, 40, -74, 42], [-74, 40, -72, 42]]
    for r in regions:
        w, s, e, n = r["box"]
        dw, ds, de, dn = r["data_box"]
        lat, lon = (s + n) / 2, (w + e) / 2
        assert build.haversine_m(lat, w, lat, dw) >= 2000
        assert build.haversine_m(s, lon, ds, lon) >= 2000
        if r["kind"] == "block":
            assert e - w == n - s == 2
            assert w % 2 == s % 2 == 0
    # Both vertices are outside: clipping must keep the crossing and the margin.
    clipped = build.clip_to_box([piece("Crossing", [[-74.2, 40.025], [-73.8, 40.025]])], m["data_box"])
    assert len(clipped) == 1
    assert clipped[0]["geometry"]["coordinates"][0][0] == pytest.approx(m["data_box"][0])
    assert clipped[0]["geometry"]["coordinates"][-1][0] == pytest.approx(m["data_box"][2])


def test_us_regions_cover_islands_dc_and_polygon_not_just_envelopes():
    # Tiny stand-ins in their real hemispheres; Alaska's two pieces must stay separate.
    states = {"AK": {"boxes": [[179.95, 51, 180, 51.05], [-180, 51, -179.95, 51.05]],
                      "geometry": {"type": "MultiPolygon", "coordinates":
                                   polygon(179.95, 51, 180, 51.05)["coordinates"] +
                                   polygon(-180, 51, -179.95, 51.05)["coordinates"]}},
              "HI": state([-155, 19, -154.95, 19.05]), "DC": state([-77.05, 38.9, -77, 38.95])}
    regions = build.us_regions([], states)
    assert len(regions) == 4
    assert {s for r in regions for s in r["states"]} == {"AK", "HI", "DC"}
    assert all(r["box"][2] - r["box"][0] == 2 for r in regions)
    islands = polygon(0, 0, .05, .05)["coordinates"] + polygon(4.1, 0, 4.15, .05)["coordinates"]
    regions = build.us_regions([], {"HI": {"boxes": [[0, 0, 4.15, .05]],
                                           "geometry": {"type": "MultiPolygon", "coordinates": islands}}})
    assert [r["box"] for r in regions] == [[0, 0, 2, 2], [4, 0, 6, 2]]


def test_hpms_state_local_join_rates_and_nearest_published_match(monkeypatch):
    states = {"NY": state([-74.1, 40, -74, 40.1]), "NJ": state([-74, 40, -73.9, 40.1])}
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    feats = [piece("Crossing", [[-74.05, 40.025], [-73.95, 40.025]]),
             piece("Uncounted", [[-74.05, 40.075], [-74.01, 40.075]], ref="I 8")]
    roads = build.group(feats, build.NameTable())
    segments = {"NY": [segment(1, [[-74.06, 40.0251], [-74, 40.0251]], 11000)],
                "NJ": [segment(2, [[-74, 40.025], [-73.94, 40.025]], 22000)]}
    report = hpms.join_by_state(roads, feats, segments)
    assert report["NY"]["eligible_groups"] == 2 and report["NY"]["joined_groups"] == 1
    assert report["NY"]["join_rate"] == .5 and report["NJ"]["join_rate"] == 1
    assert roads["crossing"]["aadt"] == 22000  # nearest; never 33000
    assert roads["uncounted"]["aadt"] is None and "uncounted" in roads
    # A NY match cannot count as a NJ match merely because the group crosses both states.
    report = hpms.join_by_state(roads, feats, {"NY": segments["NY"], "NJ": []})
    assert report["NJ"]["join_rate"] == 0


@pytest.mark.parametrize("code,name,refs,hpms_name,number,sign,joins", [
    ("AL", "11th Street North", [], "11th St N", 3131, 6, True),
    ("AL", "South Craft Highway", [], "S Craft Hwy", 2073, 1, True),
    ("GA", "Abernathy Road Northeast", [], "Abernathy Rd NE", None, 1, True),
    ("DC", "11th Street Northeast", [], "11TH ST NE", None, 1, True),
    ("DC", "11th Street Southeast", [], "11TH ST NE", None, 1, False),
    ("DC", "16th Street", ["MD-390"], "16TH ST NW", None, 1, True),
    ("DC", "Bladensburg Road", ["US 1 ALT", "MD-450"], "BLADENSBURG RD NE", None, 1, True),
    ("DC", "Chevy Chase Circle", [], "CHEVY CHASE CIR NW", None, 1, True),
    ("DC", "19th Street Northwest", [], "CONSTITUTION AVE NW", 50, 3, False),
    ("DC", "South Capitol Street", [], "SOUTH CAPITOL ST BN", None, 1, True),
    ("GA", "11th Street", [], "11TH ST NE", None, 1, False),  # quadrants are optional only in DC
    ("AK", "Chief Eddie Hoffman Highway", [], "Chief Eddie Hoffman Highway (Bethel)", None, 1, True),
    ("AK", "Chiniak Highway", [], "Chiniak Highway (Kodiak)", None, 1, True),
    ("AK", "Chiniak Highway", [], "Pasagshak Road (Kodiak)", None, 1, False),
    ("AK", "East Benson Boulevard", [], "Benson Boulevard (Anchorage)", None, 1, False),
    ("GA", "Chiniak Highway", [], "Chiniak Highway (Kodiak)", None, 1, False),
    ("CA", "10th Street West", [], "LA_CO_10TH ST W", None, 1, True),
    ("CA", "14th Street", [], "RIV_RIV_14TH ST", None, 1, True),
    ("CA", "11th Street", [], "ALA_OAK_11TH ST", None, 1, True),
    ("CA", "Adoree Street", [], "LA_NRW_NORWALK BLVD", None, 1, False),
    ("CA", "16th Street", [], "SBD_UPL_E 16TH ST", None, 1, False),
    ("CA", "East Capitol Avenue", [], "SCL_SJS_N CAPITOL AVE", None, 1, False),
    ("GA", "14th Street", [], "RIV_RIV_14TH ST", None, 1, False),
    ("GA", "Clark Street", ["US 27 Alternate", "GA-16", "GA-34", "US-29", "GA-14"], None, 29, 3, True),
    ("GA", "SR-40", ["SR-40"], None, 40, 4, True),  # the study preserves existing first-ref SR matches
    ("IA", "East Lakeshore Drive", ["US 71 Business", "IA-7"], "IA 7 E", 7, 4, True),
    ("AL", "I-20", ["I-20", "I-59", "US-80"], "Skyland Blvd E", 59, 2, True),
    ("CA", "South Malibu Canyon Road", ["CR N1", "N1"], None, 1, 4, False),
    ("CA", "South Malibu Canyon Road", ["N1"], None, 1, 4, False),  # unsafe as the first ref too
    ("CA", "CA-1", ["CA-1"], None, 1, 4, True),
    ("CA", "Arroyo Seco Parkway", ["CA-110", "US 66 Hist"], None, 110, 2, False),
    ("AL", "24th Street South", ["US-78"], "2nd Ave S", 78, 6, False),
    ("AL", "Alabama Highway 24", ["AL-24"], None, 24, 1, False),
    ("AL", "20th Avenue", ["US-29"], None, None, None, False),
])
def test_hpms_study_identifier_joins_and_rejections(monkeypatch, code, name, refs, hpms_name, number, sign, joins):
    # Real study identifiers on coincident geometry: only identity decides the join.
    monkeypatch.setattr(hpms, "load_states", lambda: {code: state([-75, 39, -73, 41])})
    coords = [[-74.08, 40.02], [-74.07, 40.02]]
    feats = [piece(name, coords, ref=";".join(refs), highway="primary")]
    roads = build.group(feats, build.NameTable())
    row = segment(1, coords, name=hpms_name, ref=number)
    row["properties"]["route_signing"] = sign
    report = hpms.join_by_state(roads, feats, {code: [row]})
    assert report[code]["joined_groups"] == int(joins)
    assert next(iter(roads.values()))["aadt"] == (10000 if joins else None)


@pytest.mark.parametrize("name,number,ref,joins", [
    ("35E", None, "I 35E", True),  # TX object 907920: no routenumber, Interstate signing
    ("35E", "35E", "I 35E", True),  # same identity with the old service's numbered shape
    ("35E", 35, "I 35E", True),
    ("I-35E", 35, "I 35E", True),
    ("I 35 E", 35, "I 35E", True),
    ("35 E", None, "I 35E", True),
    ("35W", None, "I 35W", True),
    ("69E", None, "I 69E", True),
    ("69W", None, "I 69W", True),
    ("69C", None, "I 69C", True),
    ("35N", None, "I 35", True),  # no signed I-35N branch
    ("69S", None, "I 69", True),
    ("35 N", None, "I 35", True),  # separate letters remain travel directions
    ("I 35 N", "35 N", "I 35", True),
    ("I 35 S", "35 S", "I 35", True),
    ("35E", None, "I 35W", False),
    ("35E", None, "I 35", False),
    ("35", None, "I 35E", False),
    ("35E Business", None, "I 35E", False),
])
@pytest.mark.parametrize("join_path", ["national", "metro"])
def test_hpms_lettered_interstate_from_name_keeps_route_identity(monkeypatch, name, number, ref, joins, join_path):
    monkeypatch.setattr(hpms, "load_states", lambda: {"TX": state([-97, 32, -96, 34])})
    coords = [[-96.81, 32.8], [-96.80, 32.8]]
    # Negative cases share the row's name: only the route-conflict guard can reject them.
    roads = {"road": {"name": "Stemmons Freeway" if joins else name, "refs": [ref]}}
    feats = [{"properties": {"g": "road"}, "geometry": {"type": "LineString", "coordinates": coords}}]
    row = segment(907920, coords, count=200996, ref=number, name=name)
    row["properties"].update(route_signing=2, facility_type=2)
    if join_path == "national":
        count = hpms.join_by_state(roads, feats, {"TX": [row]})["TX"]["joined_groups"]
    else:
        count = hpms.join(roads, feats, [row])
    assert count == int(joins)
    assert roads["road"].get("aadt") == (200996 if joins else None)


@pytest.mark.parametrize("join_path", ["national", "metro"])
@pytest.mark.parametrize("code,name,number,sign,ref", [
    ("CA", "005S", None, 2, "I 5"),  # BTS OBJECTID 627983: I-5 truck bypass
    ("CA", "5S", None, 2, "I 5"),
    ("CA", "I 5S", 5, 2, "I 5"),
    ("CA", "I 5", "5S", 2, "I 5"),
    ("CA", "I 5 S", "5S", 2, "I 5"),
    ("NY", "US 9W", 9, 3, "US 9"),
    ("NY", "NY 9N", 9, 4, "NY 9"),
    ("FL", "US-19A", 19, 3, "US 19"),
])
def test_hpms_attached_route_letter_never_supplies_mainline_count(monkeypatch, join_path, code, name, number, sign, ref):
    # Equal names and aligned lines expose both route and name join paths.
    # Stripping an attached direction-like suffix must fail this test.
    monkeypatch.setattr(hpms, "load_states", lambda: {code: state([-75, 39, -73, 41])})
    coords = [[-74.08, 40.02], [-74.07, 40.02]]
    row = segment(627983, coords, count=17500, name=name, ref=number)
    row["properties"].update(route_signing=sign, facility_type=2)
    features = [{"properties": {"g": "road"}, "geometry": row["geometry"]}]
    roads = {"road": {"name": name, "refs": [ref]}}
    if join_path == "national":
        count = hpms.join_by_state(roads, features, {code: [row]})[code]["joined_groups"]
    else:
        count = hpms.join(roads, features, [row])
    assert count == 0
    assert roads["road"].get("aadt") is None


@pytest.mark.parametrize("direction", ["N", "S", "E", "W"])
@pytest.mark.parametrize("join_path", ["national", "metro"])
def test_hpms_interstate_travel_direction_keeps_mainline(monkeypatch, direction, join_path):
    monkeypatch.setattr(hpms, "load_states", lambda: {"IA": state([-94, 41, -93, 42])})
    # IA OBJECTID 59279: real BTS field/geometry shape, with its travel direction varied.
    coords = [[-93.78004160599994, 41.47652681400007],
              [-93.78004672299994, 41.47697483200005],
              [-93.78004543099996, 41.47740618500006]]
    row = segment(59279, coords, count=24900, name=f"I 35 {direction}", ref=35)
    row["properties"].update(route_signing=2, facility_type=1, f_system=1,
                             aadt_single_unit=1841, aadt_combination=4920)
    roads = {"road": {"name": "I-35", "refs": ["I 35"]}}
    feats = [{"properties": {"g": "road"}, "geometry": row["geometry"]}]
    if join_path == "national":
        count = hpms.join_by_state(roads, feats, {"IA": [row]})["IA"]["joined_groups"]
    else:
        count = hpms.join(roads, feats, [row])
    # E/W designate reviewed branches of 35; only N/S describe this mainline.
    assert count == int(direction in ("N", "S"))
    assert roads["road"].get("aadt") == (24900 if direction in ("N", "S") else None)


@pytest.mark.parametrize("name,ref,alias,sign,code,joins", [
    ("George Parks Highway", "AK-3", "A4", 2, "AK", True),
    ("Richardson Highway", "AK-4", "A4", 2, "AK", False),
    ("AK-1", "AK-1", "A3", 2, "AK", True),
    ("Alaska Highway", "AK-2", "A2", 2, "AK", True),
    ("Glenn Highway", "AK-1", "A1", 2, "AK", True),
    ("Alaska Highway", "AK-2", "A1", 2, "AK", True),
    ("Richardson Highway", "AK-4", "A1", 2, "AK", True),
    ("George Parks Highway", "AK-3", "A4", 4, "AK", False),
    ("George Parks Highway", "AK-3", "A4", 2, "CA", False),
])
def test_hpms_alaska_interstate_crosswalk(monkeypatch, name, ref, alias, sign, code, joins):
    monkeypatch.setattr(hpms, "load_states", lambda: {code: state([-151, 60, -148, 63])})
    coords = [[-150, 61], [-149.99, 61]]
    feats = [piece(name, coords, ref=ref)]
    roads = build.group(feats, build.NameTable())
    row = segment(1, coords, name=alias, ref=None)
    row["properties"]["route_signing"] = sign
    report = hpms.join_by_state(roads, feats, {code: [row]})
    assert report[code]["joined_groups"] == int(joins)


@pytest.mark.parametrize("coords,joins", [
    ([[-150, 60.999], [-150, 61.001]], False),  # a crossing supplies no aligned length
    ([[-150, 61.00011], [-149.99, 61.00011]], False),  # 12 m offset: inside 30, outside 10
    ([[-150, 61], [-149.9992, 61]], False),  # 43 m overlap, below 50
    ([[-150, 61], [-149.9992, 61], [-150, 61], [-149.9992, 61]], False),  # repeats cannot add support
    ([[-149.995482, 60.9999325], [-149.994518, 61.0000675]], False),  # >15°, >50 m overlap, <10 m offsets
    ([[-150, 61], [-149.998, 61]], True),
])
def test_hpms_alaska_alias_requires_aligned_overlap(monkeypatch, coords, joins):
    monkeypatch.setattr(hpms, "load_states", lambda: {"AK": state([-151, 60, -148, 63])})
    feats = [piece("George Parks Highway", [[-150, 61], [-149.99, 61]], ref="AK-3")]
    roads = build.group(feats, build.NameTable())
    row = segment(1, coords, name="A4", ref=None)
    report = hpms.join_by_state(roads, feats, {"AK": [row]})
    assert report["AK"]["joined_groups"] == int(joins)


def test_hpms_new_name_match_keeps_30_m_limit(monkeypatch):
    monkeypatch.setattr(hpms, "load_states", lambda: {"DC": state([-75, 39, -73, 41])})
    feats = [piece("11th Street Northeast", [[-74.08, 40.02], [-74.07, 40.02]], ref="")]
    roads = build.group(feats, build.NameTable())
    row = segment(1, [[-74.08, 40.0203], [-74.07, 40.0203]], name="11TH ST NE", ref=None)
    assert hpms.join_by_state(roads, feats, {"DC": [row]})["DC"]["joined_groups"] == 0


# GA-141's count row is a ramp, but the frozen row predates the fetch of facility_type: it
# passes once the fixture is refreshed from a fresh HPMS fetch (strict, so it can't be forgotten).
NEEDS_FACILITY_TYPE = {"ga-141-2"}
REVIEW_CASES = [pytest.param(c, id=c["key"], marks=pytest.mark.xfail(
    strict=True, reason="needs facility_type from a fresh HPMS fetch")) if c["key"] in NEEDS_FACILITY_TYPE
    else pytest.param(c, id=c["key"])
    for c in json.loads((ROOT / "tests/fixtures/roads/hpms-review.json").read_text())]


@pytest.mark.parametrize("case", REVIEW_CASES)
def test_hpms_review_real_alignment_cases(case):
    # Frozen public HPMS/OSM geometry from the 29-case independent review.
    key, code = case["key"], case["state"]
    roads = {key: dict(case["road"])}
    features = [{"type": "Feature", "properties": {"g": key},
                 "geometry": {"type": "LineString", "coordinates": line}} for line in case["lines"]]
    report = hpms.join_by_state(roads, features, {code: [case["row"]]})
    assert report[code]["joined_groups"] == int(case["joins"])
    assert roads[key]["aadt"] == (case["row"]["properties"]["aadt"] if case["joins"] else None)


@pytest.mark.parametrize("ref", ["US 101 Spur", "US 101 Business", "US 101 Bus", "US 101 Alternate",
    "US 101 Alt", "US 101 Truck", "US 101 Bypass", "US 101 Byp", "US 101 Loop",
    "US 101 Hist", "US 101 Unco", "US 101 Detour", "US 101 Old",
    # Frontage roads coded with the mainline's number (WY rows 1678 and 71408 put their counts on I-25 and I-80),
    # and NC's short spellings of Truck and Connector (rows 179618, 298903).
    "US 101 Service Road", "US 101 Svc Rd", "US 101 Frontage Road", "US 101 Frtg Rd", "US 101 Trk", "US 101 Cnctr"])
@pytest.mark.parametrize("variant_on_road", [True, False])
def test_hpms_route_variants_never_match_mainline(monkeypatch, ref, variant_on_road):
    monkeypatch.setattr(hpms, "load_states", lambda: {"CA": state([-75, 39, -73, 41])})
    coords = [[-74.08, 40.02], [-74.07, 40.02]]
    features = [piece("Bayshore Freeway", coords, ref=ref if variant_on_road else "US-101")]
    roads = build.group(features, build.NameTable())
    row = segment(636400, coords, ref=101, name="Bayshore Freeway" if variant_on_road else ref)
    row["properties"]["route_signing"] = 3
    assert hpms.join_by_state(roads, features, {"CA": [row]})["CA"]["joined_groups"] == 0


@pytest.mark.parametrize("code,name,other", [
    ("DC", "N Street NW", "North Street NW"),
    ("AK", "Old (Upper) Road", "Old Road"),
    ("AK", "Old (Upper) Road", "Old (Lower) Road"),
])
def test_hpms_name_qualifiers_remain_distinct(code, name, other):
    assert not hpms.us_names_match(hpms.normalize_us_name(name, code),
                                   hpms.normalize_us_name(other, code), code)


@pytest.mark.parametrize("join_path", ["national", "metro"])
@pytest.mark.parametrize("name,number,refs,joins", [
    ("US93N BUSINESS", 93, ["US-93"], False),
    ("BUS US 93", 93, ["US-93"], False),
    ("US-93 BUS", 93, ["US-93"], False),
    ("US93BUSINESS", 93, ["US-93"], False),
    ("U.S. Highway 93 Spur", 93, ["US-93"], False),
    ("Business US 93", 93, ["US-93"], False),
    ("US 93 Business", 93, ["SR-22", "US-93"], False),
    ("US 93", "93N BUSINESS", ["US-93"], False),
    ("US 93", "BUS US 93", ["US-93"], False),
    ("US 93 Business", None, ["US-93"], False),
    ("US93N BUSINESS", 93, ["US 93 BUS"], True),
    ("US93N", 93, ["US-93"], True),
])
def test_hpms_nevada_variant_cannot_bypass_identity_via_name(join_path, name, number, refs, joins):
    # Real Nevada object 5521 supplies the spelling and aligned geometry. Equal
    # names expose the metro name fallback, including a conflicting second ref.
    row = json.loads((ROOT / "tests/fixtures/roads/hpms-nv-business.json").read_text())
    row["properties"].update(route_name=name, route_number=number)
    roads = {"road": {"name": name, "refs": refs}}
    feats = [{"type": "Feature", "properties": {"g": "road"}, "geometry": row["geometry"]}]
    if join_path == "national":
        count = hpms.join_by_state(roads, feats, {"NV": [row]})["NV"]["joined_groups"]
    else:
        count = hpms.join(roads, feats, [row])
    assert count == int(joins)


@pytest.mark.parametrize("join_path", ["national", "metro"])
def test_hpms_ramp_facility_never_supplies_a_surface_road_count(join_path):
    case = next(c for c in json.loads((ROOT / "tests/fixtures/roads/hpms-review.json").read_text())
                if c["key"] == "ga-141-2")
    # The original cache omitted facility_type. Supply FHWA's ramp code here;
    # the raw geometry/identifiers reproduce the reviewed GA-141 false join.
    row = dict(case["row"], properties=dict(case["row"]["properties"], facility_type=4))
    roads = {case["key"]: dict(case["road"])}
    feats = [{"properties": {"g": case["key"]},
              "geometry": {"type": "LineString", "coordinates": ln}} for ln in case["lines"]]
    if join_path == "national":
        count = hpms.join_by_state(roads, feats, {"GA": [row]})["GA"]["joined_groups"]
    else:
        count = hpms.join(roads, feats, [row])
    assert count == 0


@pytest.mark.parametrize("old_identity", ["name", "route"])
@pytest.mark.parametrize("join_path", ["national", "metro"])
def test_hpms_alignment_also_rejects_old_rule_crossings(old_identity, join_path):
    case = next(c for c in json.loads((ROOT / "tests/fixtures/roads/hpms-review.json").read_text())
                if c["key"] == "ute-avenue")
    # Use the same real crossing with a first-ref route or an exact HPMS name:
    # both were admitted before Task 15d's normalization/all-ref changes.
    road = dict(case["road"], refs=["US-50"] if old_identity == "route" else [],
                name="U.S. 50" if old_identity == "name" else "Ute Avenue")
    feats = [{"type": "Feature", "properties": {"g": "ute"},
              "geometry": {"type": "LineString", "coordinates": ln}} for ln in case["lines"]]
    if join_path == "national":
        assert hpms.join_by_state({"ute": road}, feats, {"CO": [case["row"]]})["CO"]["joined_groups"] == 0
    else:
        assert hpms.join({"ute": road}, feats, [case["row"]]) == 0


@pytest.mark.parametrize("hpms_length,overlap,repeats,joins", [
    (1000, 99, 1, False), (1000, 101, 1, True),
    (50, 39, 1, False), (50, 41, 1, True),
    (1.13, 1.13, 1, False), (24, 24, 1, False),
    (30, 24, 1, False), (30, 26, 1, True),
    (1000, 60, 4, False),  # duplicate OSM pieces must not inflate support past 100 m
])
def test_hpms_alignment_length_and_share(monkeypatch, hpms_length, overlap, repeats, joins):
    monkeypatch.setattr(hpms, "load_states", lambda: {"CA": state([-1, -1, 1, 1])})
    from math import pi
    degrees_per_m = 180 / pi / hpms.EARTH_R_M
    line = [[0, 0], [overlap * degrees_per_m, 0]]
    row = segment(1, [[0, 0], [hpms_length * degrees_per_m, 0]], ref=101)
    row["properties"]["route_signing"] = 3
    feats = [{"type": "Feature", "properties": {"g": "road"},
              "geometry": {"type": "LineString", "coordinates": line}} for _ in range(repeats)]
    roads = {"road": {"name": "Highway", "refs": ["US-101"]}}
    assert hpms.join_by_state(roads, feats, {"CA": [row]})["CA"]["joined_groups"] == int(joins)


@pytest.mark.parametrize("second_state", ["NY", "NJ"])
def test_hpms_equal_distance_prefers_route_match_then_lower_object_id(monkeypatch, second_state):
    states = {code: state([-75, 39, -73, 41]) for code in ("NY", "NJ")}
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    coords = [[-74.08, 40.02], [-74.02, 40.02]]
    feats = [piece("Crossing", coords)]
    roads = build.group(feats, build.NameTable())
    name_only = segment(1, coords, ref=8)
    higher_route_id = segment(20, coords, name="Other", count=9000)
    lower_route_id = segment(3, coords, name="Other", count=10000)
    lower_route_id["properties"]["aadt_combination"] = 500
    rows = {second_state: [name_only, higher_route_id]}
    rows.setdefault("NY", []).append(lower_route_id)
    hpms.join_by_state(roads, feats, rows)
    assert (roads["crossing"]["aadt"], roads["crossing"]["trucks_pct"]) == (10000, 6)


def test_simplified_border_uses_the_valid_count_from_either_eligible_state(monkeypatch):
    # Outward simplified coverages overlap. NJ's simplified line clips away
    # the west part of this NJ road, but still intersects the group's east end.
    states = {"NY": state([-74.1, 40, -73.99, 40.1]),
              "NJ": state([-74.001, 40, -73.9, 40.1])}
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    feats = [piece("Crossing", [[-74.05, 40.025], [-73.995, 40.025]])]
    roads = build.group(feats, build.NameTable())
    report = hpms.join_by_state(roads, feats, {
        "NY": [segment(1, [[-74.045, 40.0251], [-74.04, 40.0251]], 11000)],
        "NJ": [segment(2, [[-74.045, 40.025], [-74.04, 40.025]], 22000)]})
    assert roads["crossing"]["aadt"] == 22000
    for code in states:
        assert report[code]["eligible_groups"] == report[code]["joined_groups"] == 1
        assert report[code]["border_groups"] == 1


def test_us_join_prunes_distance_checks_on_400_edges(monkeypatch):
    monkeypatch.setattr(hpms, "load_states", lambda: {"NY": state([-75, 39, -73, 41])})
    coords = [[-74 + i * .001, 40] for i in range(401)]
    feats = [piece("Crossing", coords)]
    roads = build.group(feats, build.NameTable())
    checks = 0
    distance = hpms._edge_distance

    def measured(*args):
        nonlocal checks
        checks += 1
        return distance(*args)

    monkeypatch.setattr(hpms, "_edge_distance", measured)
    report = hpms.join_by_state(roads, feats, {"NY": [segment(1, coords, 30000)]})
    assert report["NY"]["join_rate"] == 1 and roads["crossing"]["aadt"] == 30000
    assert 0 < checks <= 4000  # at most ten nearby HPMS edges per road edge, not 160,000


def test_write_region_counted_format_and_class_defaults(tmp_path):
    meta = build.us_regions([], {"NY": state([-74, 40, -73.95, 40.05])})[0]
    entry = build.write_region(meta, {}, [], tmp_path, BUILT, "2026-11-21")
    doc = json.loads(gzip.decompress((tmp_path / entry["file"]).read_bytes()))
    assert doc["roads"] == {} and doc["level"] == entry["level"] == "counted"
    assert entry["box"] == meta["box"] and entry["data_box"] == meta["data_box"]
    feats = [piece("Counted Freeway", [[-73.99, 40.01], [-73.98, 40.01]])]
    roads = build.group(feats, build.NameTable())
    roads["counted-freeway"].update({"slow": [[7, 21]], "links": [{"id": "nyc-only"}]})
    entry = build.write_region(meta, roads, feats, tmp_path, BUILT, "2026-11-21")
    road = json.loads(gzip.decompress((tmp_path / entry["file"]).read_bytes()))["roads"]["counted-freeway"]
    assert "slow" not in road and "links" not in road and road["aadt"] is None


def test_us_index_lists_blocks_with_digests_preserves_nyc_and_overlapping_metro(tmp_path, monkeypatch):
    # Isolate state/block handling on one synthetic metro.
    # The real 50-metro release gate is exercised separately below.
    monkeypatch.setattr(build.check_us, "check_build", lambda *args: None)
    monkeypatch.setattr(build.check_us, "preflight", lambda *args: None)
    states = {s: state([-74.1, 40, -73.95, 40.1]) for s in hpms.US_STATES}
    # Two envelopes: a valid HI count from the first must be discarded when the second fails.
    states["HI"]["boxes"] = [[-74.1, 40, -73.95, 40.1], [179.9, 0, 180, .1]]
    # Keep the geometry tiny while verifying that all 50 states and DC are requested.
    monkeypatch.setattr(build, "load_us_regions", lambda: ([metro()], states))
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    src = tmp_path / "tiny.geojsonseq"
    feats = [piece("Crossing", [[-74.2, 40.025], [-73.8, 40.025]])]
    src.write_text("\n".join(json.dumps(f) for f in feats) + "\n")
    osmium_timeouts = []
    monkeypatch.setattr(build, "run_osmium", lambda pbf, work, **kw: osmium_timeouts.append(kw.get("timeout_s")) or src)
    monkeypatch.setattr(build, "osm_date_of", lambda pbf: "2026-11-21")
    calls = []
    hi_boxes = []

    fetch_state = hpms.fetch_state

    def fetch(code, boxes, work, hpms_file):
        calls.append(code)
        return fetch_state(code, boxes, work, hpms_file)

    def read_box(path, code, box):
        if code == "HI":
            hi_boxes.append(box)
            if len(hi_boxes) == 2:
                raise RuntimeError("state layer missing on second box")
            return [segment(99, [[-74.2, 40.025], [-73.8, 40.025]], count=99000)]
        # A count away from the road still contributes state eligibility.
        return [segment(1, [[-74.09, 40.09], [-74, 40.09]])] if code == "NY" else []

    monkeypatch.setattr(hpms, "_read_box", read_box)
    monkeypatch.setattr(hpms, "fetch_state", fetch)
    out = tmp_path / "out"
    old = build.write_metro({**build.metro("nyc"), "box": [-74.09, 40.02, -74.08, 40.03]},
                            {}, [], out, BUILT, "2026-11-21")
    build.write_index([old], out)
    result = build.build_us(tmp_path / "us.osm.pbf", out, tmp_path / "work", BUILT, hpms_file=HPMS_FIXTURE)
    assert result == json.loads((out / "index.json").read_text())
    assert result["metros"][0] == old
    us = result["metros"][1:]
    assert len(us) == 3 and all(e["level"] == "counted" for e in us)
    assert set(calls) == set(hpms.US_STATES) and len(calls) == 51
    m = next(e for e in us if e["kind"] == "metro")
    assert m["file"] == "us-12345-2026-11.json.gz" and m["cbsa"] == "12345"
    for e in us:
        assert e["kind"] in ("metro", "block") and "archive" not in e
        member = (out / e["file"]).read_bytes()
        assert len(member) == e["bytes"] and hashlib.sha256(member).hexdigest() == e["sha256"]
        doc = json.loads(gzip.decompress(member))
        assert doc["kind"] == e["kind"] and doc["box"] == e["box"] and doc["data_box"] == e["data_box"]
        assert all("slow" not in r and r["aadt"] is None for r in doc["roads"].values())
    blocks = [e for e in us if e["kind"] == "block"]
    assert [e["box"] for e in blocks] == [[-76, 40, -74, 42], [-74, 40, -72, 42]]
    assert blocks[0]["box"][0] < m["box"][0] < m["box"][2] < blocks[0]["box"][2]
    assert not list(out.glob("*.zip"))
    report = json.loads((out / "reports/us-build.json").read_text())
    assert report["states"]["HI"]["status"] == "error" and "missing" in report["states"]["HI"]["error"]
    assert len(hi_boxes) == 2
    assert report["states"]["HI"]["pages"] == 2 and report["states"]["HI"]["rows"] == 1
    assert not hpms.state_cache("HI", hi_boxes, tmp_path / "work").exists()
    assert report["states"]["HI"]["join_rate"] is None and report["errors"]
    assert report["states"]["NJ"] == {"source": hpms.HPMS_SOURCE, "year": 2024, "pages": 1, "rows": 0, "status": "ok", "eligible_groups": 0,
                                     "joined_groups": 0, "border_groups": 0, "join_rate": None}
    assert report["states"]["NY"]["join_rate"] == 0
    assert report["states"]["NY"]["border_groups"] == report["states"]["NY"]["eligible_groups"]
    assert osmium_timeouts == [10800]
    assert report["peak_disk_bytes"] > 0 and report["block_count"] == 2 and report["metro_count"] == 1
    sizes = sorted(e["bytes"] for e in blocks)
    assert report["block_bytes"] == {"min": sizes[0], "median": sum(sizes) / 2, "max": sizes[-1]}
    assert report["splits_by_level"] == {} and report["index_bytes"] == (out / "index.json").stat().st_size
    assert "archive_bytes" not in report and "square_count" not in report
    assert "input_download" in report["timings_s"] and "hpms" in report["timings_s"]
    assert src.is_file() and not list((tmp_path / "work").glob("us-build-*"))


def test_us_cli_dispatches_and_rejects_speed_or_clipping_flags(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(build, "build_us", lambda *args, **kw: called.append((args, kw)) or {"metros": []})
    assert build.main(["us", "us.osm.pbf", "--out", str(tmp_path / "out"), "--work", str(tmp_path / "work"), "--hpms-file", str(HPMS_FIXTURE)]) == 0
    assert called[0][0][:3] == (Path("us.osm.pbf"), tmp_path / "out", tmp_path / "work")
    assert called[0][1] == {"timeout_s": 10800, "hpms_file": HPMS_FIXTURE}
    assert build.main(["us", "us.osm.pbf", "--out", str(tmp_path / "out"), "--work", str(tmp_path / "work"),
                       "--timeout", "12345", "--hpms-file", str(HPMS_FIXTURE)]) == 0
    assert called[1][1] == {"timeout_s": 12345, "hpms_file": HPMS_FIXTURE}
    with pytest.raises(SystemExit):
        build.main(["us", "us.osm.pbf", "--out", str(tmp_path), "--no-counts"])
    for value in ("0", "-1", "nan", "inf"):
        with pytest.raises(SystemExit):
            build.main(["us", "us.osm.pbf", "--out", str(tmp_path), "--work", str(tmp_path), "--timeout", value])


@pytest.mark.parametrize(("max_bytes", "count", "splits"), [(1, 16, {"0": 1, "1": 4}),
                                                            (4_000_000, 1, {})])
def test_dense_blocks_split_from_original_bucket_and_stop_at_half_degree(tmp_path, monkeypatch, max_bytes, count, splits):
    # A road in every final quarter forces both split levels; even an oversized
    # half-degree file must terminate, and the provisional parents must disappear.
    states = {"NY": state([0, 0, 2, 2])}
    monkeypatch.setattr(build, "BLOCK_MAX_B", max_bytes, raising=False)
    monkeypatch.setattr(build, "load_us_regions", lambda: ([], states))
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    monkeypatch.setattr(hpms, "fetch_state", lambda *args: [])
    monkeypatch.setattr(build.check_us, "preflight", lambda *args: None)
    monkeypatch.setattr(build.check_us, "check_build", lambda *args: None)
    monkeypatch.setattr(build, "osm_date_of", lambda pbf: "2026-11-21")
    features = [piece(f"Road {x} {y}", [[x + .20, y + .25], [x + .30, y + .25]],
                      ref=None, highway="primary") for x in (0, .5, 1, 1.5) for y in (0, .5, 1, 1.5)]
    # This piece is outside a quarter's core but inside its margin.
    features.append(piece("Margin Road", [[.501, .24], [.501, .26]], ref=None, highway="primary"))
    src = tmp_path / "dense.geojsonseq"
    src.write_text("\n".join(json.dumps(f) for f in features))
    monkeypatch.setattr(build, "run_osmium", lambda *args, **kw: src)
    out = tmp_path / "out"
    index = build.build_us(tmp_path / "us.pbf", out, tmp_path / "work", BUILT, hpms_file=HPMS_FIXTURE)
    blocks = index["metros"]
    assert index["coverage"] == {"US": [[0, 0, 2, 2]]}  # parent remains when its files split
    assert len(blocks) == count and all(e["kind"] == "block" for e in blocks)
    assert len(list(out.glob("*.json.gz"))) == count and not list(out.glob("*.zip"))
    assert len({e["id"] for e in blocks}) == count
    assert all(e["box"][2] - e["box"][0] == (0.5 if count == 16 else 2) for e in blocks)
    southwest = next(e for e in blocks if e["box"][:2] == [0, 0])
    doc = json.loads(gzip.decompress((out / southwest["file"]).read_bytes()))
    assert "Margin Road" in [r["name"] for r in doc["roads"].values()]
    if count == 16:
        assert set(doc["roads"]) == {"road-0-0", "margin-road"}
        assert all(e["bytes"] > max_bytes for e in blocks)
    report = json.loads((out / "reports/us-build.json").read_text())
    assert report["block_count"] == count and report["splits_by_level"] == splits


@pytest.mark.parametrize("kind, degrees", [("metro", 2), ("block", 2), ("block", .5)])
def test_undownloadable_region_fails_before_replacing_previous_output(tmp_path, monkeypatch, kind, degrees):
    states = {"NY": state([0, 0, 2, 2])}
    meta = build._block_region([0, 0, degrees, degrees], {(0, 0): {"NY"}})
    if kind == "metro":
        meta.update(metro([0, 0, 2, 2]), kind="metro", country="US", level="counted")
    monkeypatch.setattr(build, "load_us_regions", lambda: ([], states))
    monkeypatch.setattr(build, "us_regions", lambda *args: [meta])
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    monkeypatch.setattr(hpms, "fetch_state", lambda *args: [])
    monkeypatch.setattr(build.check_us, "preflight", lambda *args: None)
    monkeypatch.setattr(build.check_us, "check_build", lambda *args: None)
    monkeypatch.setattr(build, "osm_date_of", lambda pbf: "2026-11-21")
    monkeypatch.setattr(build, "ROAD_FILE_MAX_BYTES", 1)
    source = tmp_path / "road.geojsonseq"
    source.write_text(json.dumps(piece("Crossing", [[.1, .2], [.3, .2]])))
    monkeypatch.setattr(build, "run_osmium", lambda *args, **kw: source)
    out = tmp_path / "out"
    out.mkdir()
    build.write_index([{"id": "us-old"}], out)
    filename = f"{meta['id']}-2026-11.json.gz"
    (out / filename).write_bytes(b"previous same-month road file")
    (out / "reports").mkdir()
    (out / "reports/us-build.json").write_text("previous report")
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    written = []
    write_region = build.write_region

    def record_region(*args, **kw):
        entry = write_region(*args, **kw)
        written.append(entry)
        return entry

    monkeypatch.setattr(build, "write_region", record_region)
    with pytest.raises(ValueError) as error:
        build.build_us(tmp_path / "us.pbf", out, tmp_path / "work", BUILT, hpms_file=HPMS_FIXTURE)
    assert len(written) == 1
    assert filename in str(error.value)
    assert f"{written[0]['bytes']} bytes" in str(error.value)
    assert "cap of 1 bytes" in str(error.value)
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    assert not list(out.glob(".us-output-*"))


def test_previous_index_releases_legacy_us_entries_before_region_work(tmp_path, monkeypatch):
    states = {"NY": state([0, 0, .1, .1])}
    out = tmp_path / "out"
    out.mkdir()
    kept = [{"id": "paris", "level": "counted", "country": "FR"},
            {"id": "nyc", "level": 1, "country": "US"}]
    legacy = [{"id": identifier, "obsolete": True} for identifier in
              ("us-12345", "us-square-0-0", "us-block-2-0-0")]
    build.write_index(kept + legacy, out)
    previous_text = (out / "index.json").read_text()
    del legacy
    refs, reads = [], []
    loads = build.json.loads

    class TrackedDict(dict):
        pass

    def track_previous(text, *args, **kw):
        doc = loads(text, *args, **kw)
        if text == previous_text:
            reads.append(True)
            doc = TrackedDict(doc)
            doc["metros"] = [TrackedDict(e) for e in doc["metros"]]
            refs.extend(weakref.ref(e) for e in doc["metros"] if e["id"].startswith("us-"))
            refs.append(weakref.ref(doc))
        return doc

    def load_regions():
        assert len(reads) == 1
        assert all(ref() is None for ref in refs)
        return [metro([0, 0, .1, .1])], states

    monkeypatch.setattr(build.json, "loads", track_previous)
    monkeypatch.setattr(build, "load_us_regions", load_regions)
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    monkeypatch.setattr(hpms, "fetch_state", lambda *args: [])
    monkeypatch.setattr(build.check_us, "preflight", lambda *args: None)
    monkeypatch.setattr(build.check_us, "check_build", lambda *args: None)
    monkeypatch.setattr(build, "osm_date_of", lambda pbf: "2026-11-21")
    source = tmp_path / "road.geojsonseq"
    source.write_text(json.dumps(piece("Crossing", [[.02, .02], [.08, .02]])))
    monkeypatch.setattr(build, "run_osmium", lambda *args, **kw: source)
    index = build.build_us(tmp_path / "us.pbf", out, tmp_path / "work", BUILT, hpms_file=HPMS_FIXTURE)
    assert index["metros"][:2] == [kept[1], kept[0]]
    assert {e["id"] for e in index["metros"][2:]} == {"us-12345", "us-block-2-0-0"}
    assert all("obsolete" not in e for e in index["metros"])
    assert len(reads) == 1


def test_continuous_named_road_keeps_geometry_and_name_in_both_quarter_margins(tmp_path, monkeypatch):
    states = {"NY": state([0, 0, 2, 2])}
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    meta = build.us_regions([], states)[0]
    features = [piece("Seam Road", [[.8, .25], [1.2, .25]], ref=None, highway="primary")]
    unsplit = list(build._write_us_region(meta, features, {}, tmp_path, BUILT, "2026-11-21", [], {}))
    parent = json.loads(gzip.decompress((tmp_path / unsplit[0][0]["file"]).read_bytes()))
    parent_name = next(iter(parent["roads"].values()))["name"]
    assert parent_name == "Seam Road"
    monkeypatch.setattr(build, "BLOCK_MAX_B", 1)
    children = list(build._write_us_region(meta, features, {}, tmp_path, BUILT, "2026-11-21", [],
                                         build.defaultdict(int)))
    assert {tuple(e["box"]) for e, _ in children} == {(.5, 0, 1, .5), (1, 0, 1.5, .5)}
    for entry, _ in children:
        doc = json.loads(gzip.decompress((tmp_path / entry["file"]).read_bytes()))
        assert [r["name"] for r in doc["roads"].values()] == [parent_name]
        line = doc["features"][0]["geometry"]["coordinates"]
        assert line[0][0] < 1 < line[-1][0]  # both children include the road beyond the seam
        if entry["box"][0] == .5:
            assert line[0] == [.8, .25] and line[-1][0] < 1.2
        else:
            assert line[0][0] > .8 and line[-1] == [1.2, .25]


def test_blocks_with_only_margin_roads_ramps_tunnels_or_no_roads_are_not_written(tmp_path, monkeypatch):
    states = {"NY": state([0, 0, 8, 2])}
    monkeypatch.setattr(build, "load_us_regions", lambda: ([], states))
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    monkeypatch.setattr(hpms, "fetch_state", lambda *args: [])
    monkeypatch.setattr(build.check_us, "preflight", lambda *args: None)
    monkeypatch.setattr(build.check_us, "check_build", lambda *args: None)
    monkeypatch.setattr(build, "osm_date_of", lambda pbf: "2026-11-21")
    tunnel = piece("Tunnel", [[4.5, .2], [4.6, .2]])
    tunnel["properties"]["tunnel"] = "yes"
    src = tmp_path / "mostly-empty.geojsonseq"
    src.write_text("\n".join(json.dumps(f) for f in [
        piece("Outside", [[-.01, .2], [-.001, .2]]),
        piece("Ramp", [[2.5, .2], [2.6, .2]], highway="motorway_link"), tunnel]))
    monkeypatch.setattr(build, "run_osmium", lambda *args, **kw: src)
    out = tmp_path / "out"
    index = build.build_us(tmp_path / "us.pbf", out, tmp_path / "work", BUILT, hpms_file=HPMS_FIXTURE)
    assert index["metros"] == []
    assert index["coverage"] == {"US": [[0, 0, 2, 2], [2, 0, 4, 2], [4, 0, 6, 2], [6, 0, 8, 2]]}
    import road_data
    monkeypatch.delenv("ROAD_DATA_DIR", raising=False)

    def get(url, **kwargs):
        data = (out / url.rsplit("/", 1)[-1]).read_bytes()
        return SimpleNamespace(status_code=200, iter_content=lambda n: [data], close=lambda: None)

    for lon, lat, expected in [(0, 0, "none"), (7.9, 1.9, "none"), (8, 1, "not_built"),
                               (1, 2, "not_built"), (-.001, 1, "not_built")]:
        path = tmp_path / "config.toml"
        path.write_text(f'[place]\nname = "Test"\nlat = {lat}\nlon = {lon}\ntimezone = "UTC"\n')
        assert road_data.setup_roads(path, None, get=get).status == expected
        assert road_data.read_outcome(tmp_path / "road-data") == expected
    assert not list(out.glob("*.json.gz")) and not list(out.glob("*.zip"))
    report = json.loads((out / "reports/us-build.json").read_text())
    assert report["block_count"] == 0 and report["block_bytes"] == {"min": None, "median": None, "max": None}


def test_us_coverage_uses_frozen_footprint_even_when_simplified_geometry_omits_land():
    # Frozen full-resolution cells include a sliver, an island and both sides
    # of the antimeridian. Neither simplified envelopes nor published files do.
    states = {"AK": {**state([0, 0, .05, .05]), "core_step": .05,
                     "core_rows": [[0, 0, 1], [0, 40, 41], [1020, -3600, -3599], [1020, 3599, 3600]]},
              "HI": {**state([20, 20, 22, 22]), "core_step": .05, "core_rows": [[0, 0, 1]]}}
    assert build.us_coverage(states) == [[-180, 50, -178, 52], [0, 0, 2, 2],
                                         [2, 0, 4, 2], [178, 50, 180, 52]]


def test_osmium_timeout_reaches_both_commands_and_metro_keeps_600(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build.shutil, "which", lambda name: "/usr/bin/osmium")
    monkeypatch.setattr(build.subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    build.run_osmium(tmp_path / "nyc.pbf", tmp_path)
    build.run_osmium(tmp_path / "us.pbf", tmp_path, timeout_s=12345)
    assert [kw["timeout"] for _, kw in calls] == [600, 600, 12345, 12345]
    assert [args[1] for args, _ in calls] == ["tags-filter", "export", "tags-filter", "export"]


@pytest.mark.parametrize("failure", ["region", "index"])
def test_failed_same_month_build_preserves_blocks_and_index(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(build.check_us, "check_build", lambda *args: None)
    monkeypatch.setattr(build.check_us, "preflight", lambda *args: None)
    states = {"NY": state([-74.1, 40, -73.9, 40.05])}
    monkeypatch.setattr(build, "load_us_regions", lambda: ([], states))
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    source = tmp_path / "crossing.geojsonseq"
    source.write_text(json.dumps(piece("Crossing", [[-74.1, 40.02], [-73.9, 40.02]])))
    monkeypatch.setattr(build, "run_osmium", lambda *args, **kw: source)
    monkeypatch.setattr(build, "osm_date_of", lambda pbf: "2026-11-21")
    monkeypatch.setattr(hpms, "fetch_state", lambda *args: [])
    out, work = tmp_path / "out", tmp_path / "work"
    build.build_us(tmp_path / "us.pbf", out, work, BUILT, hpms_file=HPMS_FIXTURE)
    before = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    write_region, write_index = build.write_region, build.write_index
    written = 0

    def fail_region(*args, **kw):
        nonlocal written
        written += 1
        if written == 2:
            raise OSError("injected failure")
        return write_region(*args, **kw)

    def fail_index(entries, folder, **kwargs):
        path = write_index(entries, folder, **kwargs)
        path.write_text("partial")
        raise OSError("injected failure")

    monkeypatch.setattr(build, "write_region" if failure == "region" else "write_index",
                        fail_region if failure == "region" else fail_index)
    with pytest.raises(OSError, match="injected failure"):
        build.build_us(tmp_path / "us.pbf", out, work, BUILT, hpms_file=HPMS_FIXTURE)
    assert {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()} == before
    assert not list(out.glob(".us-output-*"))


def test_freeze_ranks_only_whole_metros_excludes_pr_and_requires_all_shapes():
    import freeze_us

    text = "SUMLEV,LSAD,CBSA,MDIV,STCOU,NAME,POPESTIMATE2025\n"
    rows = [f"310,Metropolitan Statistical Area,{10000+i},,,Metro {i} - NY,{1000-i}" for i in range(51)]
    rows += ["310,Micropolitan Statistical Area,90000,,,Micro - NY,9999999",
             "310,Metropolitan Statistical Area,90001,,,Puerto Rico - PR,9999999",
             "314,Metropolitan Division,90002,,,Division - NY,9999999",
             "050,Metropolitan Statistical Area,90003,,36001,County - NY,9999999"]
    top = freeze_us.select_metros((text + "\n".join(rows)).encode())
    assert len(top) == len({m["cbsa"] for m in top}) == 50
    assert top[0]["cbsa"] == "10000" and top[-1]["cbsa"] == "10049"
    shapes = {m["cbsa"]: polygon(-75, 40, -74, 41) for m in top}
    frozen = freeze_us.metro_regions(top, shapes)
    assert frozen[0]["box"] == [-75, 40, -74, 41] and frozen[0]["states"] == ["NY"]
    del shapes["10049"]
    with pytest.raises(ValueError, match="10049"):
        freeze_us.metro_regions(top, shapes)


def test_freeze_splits_antimeridian_without_world_spanning_rings():
    import freeze_us

    geom = freeze_us.split_antimeridian(polygon(179.9, 51, -179.9, 51.1))
    assert len(geom["coordinates"]) == 2
    boxes = freeze_us.geometry_boxes(geom)
    assert sorted(boxes) == [[-180, 51, -179.9, 51.1], [179.9, 51, 180, 51.1]]


def test_freeze_reads_latin1_population_names():
    import freeze_us

    text = "SUMLEV,LSAD,CBSA,NAME,POPESTIMATE2025\n" + "\n".join(
        f"310,Metropolitan Statistical Area,{10000+i},Cañon City {i} - CO,{1000-i}" for i in range(50))
    top = freeze_us.select_metros(text.encode("latin-1"))
    assert top[0]["name"] == "Cañon City 0 - CO" and top[-1]["cbsa"] == "10049"


def test_freeze_selects_whole_metros_without_sumlev():
    import freeze_us

    text = "CBSA,MDIV,STCOU,NAME,LSAD,POPESTIMATE2025\n" + "\n".join(
        f'{10000+i},,,"Metro {i}, NY-NJ",Metropolitan Statistical Area,{1000-i}' for i in range(51))
    text += '\n90000,,,"Micro, NY",Micropolitan Statistical Area,9999999'
    text += '\n90001,90002,,"Division, NY",Metropolitan Division,9999999'
    text += '\n90003,,36001,"County, NY",County or equivalent,9999999'
    text += '\n90004,,,"Puerto Rico, PR",Metropolitan Statistical Area,9999999'
    top = freeze_us.select_metros(text.encode())
    assert len(top) == 50 and [m["cbsa"] for m in top] == [str(10000+i) for i in range(50)]
    assert all(m["states"] == ["NY", "NJ"] for m in top)


def tiger_zip(path, records, fields, rings=None):
    """Tiny Polygon SHP+DBF files, including multipart rings when supplied."""
    shapes, dbf_rows = [], []
    rings = rings or [[[-75, 40], [-75, 41], [-74, 41], [-74, 40], [-75, 40]]]
    points = [p for ring in rings for p in ring]
    box = [min(p[0] for p in points), min(p[1] for p in points),
           max(p[0] for p in points), max(p[1] for p in points)]
    starts, offset = [], 0
    for ring in rings:
        starts.append(offset)
        offset += len(ring)
    for i, row in enumerate(records, 1):
        content = struct.pack("<I4dII", 5, *box, len(rings), len(points)) + struct.pack(f"<{len(starts)}I", *starts)
        content += b"".join(struct.pack("<dd", *p) for p in points)
        shapes.append(struct.pack(">II", i, len(content) // 2) + content)
        dbf_rows.append(b" " + b"".join(str(row[k]).encode().ljust(width) for k, width in fields))
    shp_header = bytearray(100)
    struct.pack_into(">I", shp_header, 0, 9994)
    struct.pack_into(">I", shp_header, 24, (100 + sum(len(s) for s in shapes)) // 2)
    struct.pack_into("<II4d", shp_header, 28, 1000, 5, *box)
    dbf_header = bytearray(32)
    dbf_header[0] = 3
    struct.pack_into("<IHH", dbf_header, 4, len(records), 33 + len(fields) * 32, 1 + sum(w for _, w in fields))
    descriptors = []
    for key, width in fields:
        d = bytearray(32)
        d[:len(key)] = key.encode()
        d[11], d[16] = ord("C"), width
        descriptors.append(d)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("tiny.shp", bytes(shp_header) + b"".join(shapes))
        z.writestr("tiny.dbf", bytes(dbf_header) + b"".join(descriptors) + b"\r" + b"".join(dbf_rows) + b"\x1a")


@pytest.mark.parametrize("reverse", [False, True])
def test_freeze_reads_unordered_rings_with_touching_holes_and_islands(tmp_path, reverse):
    import freeze_us
    import us_geometry as ug

    # Atlanta/Albertville have a hole whose first vertex touches its exterior.
    outer = [[0, 0], [0, 6], [6, 6], [6, 0], [0, 0]]
    touching_hole = [[6, 1], [6, 2], [5, 1], [6, 1]]
    hole = polygon(1, 1, 4, 4)["coordinates"][0][0]
    island = polygon(2, 2, 3, 3)["coordinates"][0][0]
    separate = polygon(10, 0, 11, 1)["coordinates"][0][0]
    rings = [island, touching_hole, separate, hole, outer]
    if reverse:
        rings = [list(reversed(r)) for r in reversed(rings)]
    path = tmp_path / "rings.zip"
    tiger_zip(path, [{"GEOID": "12060"}], [("GEOID", 5)], rings)
    [(row, geom)] = freeze_us.read_shapes(path)
    assert row["GEOID"] == "12060"
    assert len(geom["coordinates"]) == 3
    assert sorted(len(p) for p in geom["coordinates"]) == [1, 1, 3]
    assert not ug.intersects_land(geom, [5.7, 1.2, 5.8, 1.3])
    assert not ug.intersects_land(geom, [1.2, 1.2, 1.3, 1.3])
    assert ug.intersects_land(geom, [2.2, 2.2, 2.3, 2.3])
    assert ug.intersects_land(geom, [10.2, .2, 10.3, .3])


def test_freeze_writes_fifty_full_envelopes_51_states_and_source_digests(tmp_path):
    import freeze_us

    population, cbsas, states = [tmp_path / n for n in ("population.csv", "cbsas.zip", "states.zip")]
    # Equal populations deliberately arrive in reverse CBSA order.
    population.write_text("SUMLEV,LSAD,CBSA,NAME,POPESTIMATE2025\n" + "\n".join(
        f"310,Metropolitan Statistical Area,{10000+i},Metro {i} - NY,1000" for i in reversed(range(51))))
    tiger_zip(cbsas, [{"GEOID": str(10000+i)} for i in range(51)], [("GEOID", 5)])
    tiger_zip(states, [{"STUSPS": code, "STATEFP": str(i).zfill(2), "NAME": code}
                       for i, code in enumerate(sorted(freeze_us.STATE_CODES))],
              [("STUSPS", 2), ("STATEFP", 2), ("NAME", 2)])
    out = tmp_path / "frozen"
    assert freeze_us.freeze(population, cbsas, states, out) == {"metros": 50, "states": 51}
    doc = json.loads((out / "us-metros.json").read_text())
    assert [m["cbsa"] for m in doc["metros"]] == [str(10000 + i) for i in range(50)]
    assert all(m["box"] == [-75, 40, -74, 41] for m in doc["metros"])
    assert doc["vintage"] == 2025 and doc["population_date"] == "2025-07-01"
    assert doc["sources"]["population"]["sha256"] == hashlib.sha256(population.read_bytes()).hexdigest()
    assert doc["sources"]["cbsas"]["url"].endswith("TIGER2025/CBSA/tl_2025_us_cbsa.zip")
    coverage = json.loads((out / "us-states.json").read_text())
    assert set(coverage["states"]) == set(hpms.US_STATES)
    assert coverage["sources"]["states"]["sha256"] == hashlib.sha256(states.read_bytes()).hexdigest()
    assert coverage["states"]["NY"]["boxes"] == [[-75, 40, -74, 41]]


def test_smaller_state_freeze_keeps_full_cores_holes_islands_and_antimeridian():
    import freeze_us
    import us_geometry as ug

    # A 0.002° coastal spur crosses the .25° grid line; plain DP drops that core.
    coast = [[0, 0], [.249, 0], [.251, .025], [.249, .05], [.249, .3], [0, .3], [0, 0]]
    hole = polygon(.05, .05, .2, .25)["coordinates"][0][0]
    full = {"type": "MultiPolygon", "coordinates": [[coast, hole]] +
            polygon(.1, .1, .15, .15)["coordinates"] +
            polygon(.401, .001, .403, .003)["coordinates"] +
            polygon(179.999, 51, -179.999, 51.1)["coordinates"]}
    full = freeze_us.split_antimeridian(full)
    frozen = freeze_us.freeze_state(full)
    # Round-trip the artifact, then exercise the actual consumer, not just metadata.
    frozen = json.loads(json.dumps(frozen))
    full_cores = ug.land_cells(full, build.SQUARE_DEG)
    regions = build.us_regions([], {"AK": frozen})
    frozen_cores = ug.state_cells(frozen, build.SQUARE_DEG)
    assert {tuple(r["box"]) for r in regions} == {(0, 0, 2, 2), (-180, 50, -178, 52), (178, 50, 180, 52)}
    assert full_cores <= frozen_cores
    assert frozen_cores == full_cores  # exact core ranges add no water-only squares
    assert (5, 0) in frozen_cores and (5, 0) not in ug.land_cells(frozen["geometry"], build.SQUARE_DEG)
    assert (1, 1) not in frozen_cores  # hole
    assert (2, 2) in frozen_cores and (8, 0) in frozen_cores  # islands
    assert not ug.intersects_land(frozen["geometry"], [.06, .06, .09, .09])
    assert ug.intersects_land(frozen["geometry"], [.11, .11, .14, .14])
    assert ug.intersects_land(frozen["geometry"], [.401, .001, .403, .003])
    assert (-3600, 1020) in frozen_cores and (3599, 1020) in frozen_cores
    assert all(b[2] - b[0] < 180 for b in frozen["boxes"])
    assert all(abs(b[0] - a[0]) < 180 for poly in frozen["geometry"]["coordinates"]
               for ring in poly for a, b in zip(ring, ring[1:]))
    assert sum(len(r) for p in frozen["geometry"]["coordinates"] for r in p) < sum(
        len(r) for p in full["coordinates"] for r in p)
    assert frozen["coverage_check"] == {"full_cores": len(full_cores), "frozen_cores": len(frozen_cores),
                                        "full_subset_frozen": True}
    with pytest.raises(ValueError, match="core step"):
        ug.state_cells(frozen, .1)  # never silently reinterpret the frozen integer grid


def test_state_simplification_preserves_a_narrow_bend_without_crossing_itself():
    import freeze_us
    import us_geometry as ug

    # Removing the small tip at (.001, .022) draws a chord across the x=0 edge.
    ring = [[0, 0], [0, .02], [-.01, .02], [.001, .022], [.03, .015],
            [.03, -.01], [0, -.01], [0, 0]]
    frozen = freeze_us.freeze_state({"type": "Polygon", "coordinates": [ring]})
    # This patch above the narrow bend remains inside the simplified ring.
    assert ug.intersects_land(frozen["geometry"], [0, .0201, .0001, .0202])


def test_land_coverage_keeps_thin_slivers_and_excludes_holes_and_metro_union():
    # Missing the cell centre must not discard a land sliver.
    regions = build.us_regions([], {"NY": state([.001, .001, .004, .004])})
    assert [r["box"] for r in regions] == [[0, 0, 2, 2]]
    outer = polygon(0, 0, 6, 6)
    outer["coordinates"][0].append([[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]])
    regions = build.us_regions([], {"NY": {"geometry": outer}})
    assert len(regions) == 8 and [2, 2, 4, 4] not in [r["box"] for r in regions]
    metros = [metro([0, 0, 1, 2]), {**metro([1, 0, 2, 2]), "cbsa": "12346", "rank": 2}]
    assert all(r["kind"] == "metro" for r in build.us_regions(metros, {"NY": state([0, 0, 2, 2])}))


def test_antimeridian_margin_retains_roads_from_other_hemisphere():
    region = build.us_regions([], {"AK": state([179.95, 51, 180, 51.05])})[0]
    clipped = build.clip_to_box([piece("Aleutian road", [[-179.999, 51.02], [-179.99, 51.02]])], region["data_box"])
    assert clipped and clipped[0]["geometry"]["coordinates"][0][0] == pytest.approx(180.001)
    # A short way crossing the meridian must not become a line around the globe.
    clipped = build.clip_to_box([piece("Crossing", [[179.99, 51.02], [-179.99, 51.02]])], region["data_box"])
    assert clipped and all(abs(f["geometry"]["coordinates"][-1][0] - f["geometry"]["coordinates"][0][0]) < .03 for f in clipped)


def test_state_local_geometry_handles_holes_and_both_outside_vertices(monkeypatch):
    geom = polygon(-74.1, 40, -74, 40.1)
    geom["coordinates"][0].append([[-74.06, 40.02], [-74.04, 40.02], [-74.04, 40.04], [-74.06, 40.04], [-74.06, 40.02]])
    monkeypatch.setattr(hpms, "load_states", lambda: {"NY": {"geometry": geom}})
    feats = [piece("Crossing", [[-74.2, 40.03], [-73.9, 40.03]])]
    roads = build.group(feats, build.NameTable())
    # This count is inside the state's hole; no state-local road is near it.
    report = hpms.join_by_state(roads, feats, {"NY": [segment(1, [[-74.055, 40.03], [-74.045, 40.03]])]})
    assert report["NY"]["eligible_groups"] == 1 and report["NY"]["joined_groups"] == 0
    assert roads["crossing"]["aadt"] is None


def test_groups_on_a_state_boundary_are_eligible_in_both_states(monkeypatch):
    states = {"NY": state([-74.1, 40, -74, 40.1]), "NJ": state([-74, 40, -73.9, 40.1])}
    monkeypatch.setattr(hpms, "load_states", lambda: states)
    coords = [[-74, 40.02], [-74, 40.08]]
    feats = [piece("Boundary road", coords)]
    roads = build.group(feats, build.NameTable())
    report = hpms.join_by_state(roads, feats, {"NY": [segment(1, coords)], "NJ": []})
    assert report["NY"]["eligible_groups"] == report["NJ"]["eligible_groups"] == 1
    assert report["NY"]["join_rate"] == 1 and report["NJ"]["join_rate"] == 0


def test_us_join_skips_invalid_count_rows_and_keeps_nearest_valid_row(monkeypatch):
    monkeypatch.setattr(hpms, "load_states", lambda: {"NY": state([-74.1, 40, -74, 40.1])})
    coords = [[-74.08, 40.02], [-74.02, 40.02]]
    feats = [piece("Crossing", coords)]
    roads = build.group(feats, build.NameTable())
    rows = [segment(1, coords, count="invalid"), segment(2, [], count=20000),
            segment(3, coords, count=30000)]
    rows[1]["geometry"]["coordinates"] = []
    report = hpms.join_by_state(roads, feats, {"NY": rows})
    assert report["NY"]["join_rate"] == 1 and roads["crossing"]["aadt"] == 30000


@pytest.mark.parametrize("join_path", ["national", "metro"])
@pytest.mark.parametrize("code,oid,number,sign,name,ref,aadt,joins", [
    ("IN", 75610, 31, 3, "US-31N", "US 31", 18444, True),
    ("DC", 149, 295, 2, "INTERSTATE 295 I BN", "I 295", 94406, True),
    ("GA", 32724, None, 3, "27AL", "US 27 Alternate", 8110, True),
    ("GA", 252981, None, 3, "27BU", "US 27 Business", 2960, True),
    ("GA", 192200, None, 4, "22SP", "GA 22 Spur", 3770, True),
    ("KY", 74962, None, 4, "KY-80X", "KY 80 Business", 2557, True),
    ("NY", 245128, 9, 3, "ULSTER AVE US 9W", "US 9W", 23311, True),
    ("NY", 245128, 9, 3, "ULSTER AVE US 9W", "US 9", 23311, False),
    ("NY", 478697, 32, 4, "N PLANK RD US 9W", "US 9W", 23319, False),
    ("FL", 187321, 19, 3, "US-19A", "US 19 Alternate", 11500, True),
    ("FL", 258183, 27, 3, "US-27 N", "US 27", 4800, True),
    ("FL", 1, 92, 3, "US-17/92 A", "US 92 Alternate", 4800, True),
    ("NY", 557809, 5, 4, "EAST-WEST ARTERIAL HWY NY 5S", "NY 5S", 19009, True),
    ("NY", 567086, 12, 4, "W MAIN ST NY 12E", "NY 12E", 6400, True),
    ("NY", 235452, 9, 4, "NY 9N", "NY 9", 3417, False),
    ("WA", 7209, 16, 4, "SR 16 Spur Gorst", "WA 16 Spur", 5755, True),
    ("WA", 7209, 16, 4, "SR 16 Spur Gorst", "WA 16", 5755, False),
    ("CA", 799616, None, 4, "178S", "CA 178", 2100, False),
    ("SC", 113201, 85, 2, "INTERSTATE 85 (BUSINESS)", "I 85 Business", 18354, True),
    ("SC", 113201, 85, 2, "INTERSTATE 85 (BUSINESS)", "I 85", 18354, False),
    ("DE", 33774, 9, 3, "US9W", "US 9", 11497, True),
    ("KY", 19448, None, 3, "US-31W BYP", "US 31W Bypass", 19784, True),
    ("KY", 174637, None, 3, "US-119S", "US 119 Spur", 5660, True),
    ("KY", 69672, 119, 3, "US-119 S", "US 119", 6644, True),
    ("NJ", 28140, 139, 4, "NJ 139 LOWER", "NJ 139", 75636, False),
    ("NJ", 18062, 35, 4, "NJ 35 Z", "NJ 35", 11263, False),
    ("LA", 53836, 0, 4, "LA 92-1", "LA 92", 9508, False),
    ("MI", 190316, 127, 3, "US 127 BR", "US 127 Business", 7103, True),
    ("WA", 137024, 101, 3, "US 101 Couplet Aberdeen", "US 101", 9047, False),

])
def test_hpms_uniform_route_tokens(monkeypatch, join_path, code, oid, number, sign, name, ref, aadt, joins):
    # A join must use the signed route identity and never substitute a route
    # mentioned in the same name or fall back to a namesake mainline.
    monkeypatch.setattr(hpms, "load_states", lambda: {code: state([-75, 39, -73, 41])})
    coords = [[-74.08, 40.02], [-74.07, 40.02]]
    row = segment(oid, coords, count=aadt, name=name, ref=number)
    row["properties"].update(route_signing=sign, facility_type=2)
    road_name = name if not joins and oid != 478697 else "Different names on the same geometry"
    roads = {"road": {"name": road_name, "refs": [ref]}}
    features = [{"properties": {"g": "road"}, "geometry": row["geometry"]}]
    if join_path == "national":
        count = hpms.join_by_state(roads, features, {code: [row]})[code]["joined_groups"]
    else:
        count = hpms.join(roads, features, [dict(row, hpms_state=code)])
    assert count == int(joins)
    assert roads["road"].get("aadt") == (aadt if joins else None)


@pytest.mark.parametrize("ref,identity", [
    ("US 31NB", (3, 31)),
    ("US31northbound", (3, 31)),
    ("US 31 NORTH BOUND", (3, 31)),
    ("US 31 WBOUND", (3, 31)),
    ("US-19A", (3, 19, "alternate")),
    ("US 19 B", (3, 19, "business")),
    ("Business U.S. Highway 19 SB", (3, 19, "business")),
    ("US19EBTRUCK", (3, 19, "truck")),
    ("US 19 W Bypass", (3, 19, "bypass")),
    ("US 19 Loop West", (3, 19, "loop")),
    ("US 19 S Spur", (3, 19, "spur")),
    ("US 30BY", (3, 30, "bypass")),
    ("I 75 BL", (2, 75, "business")),
    ("NJ 139 Lower", (4, 139, "lower")),
    ("LA 92-1", (4, 92, "-1")),
    ("OR 99W", (4, 99, "w")),
    ("KY 15S", (4, 15, "spur")),
    ("I 5 S", (2, 5)),
    ("US 31W Bypass", (3, 31, "w bypass")),
    ("I 35E NB", (2, 35, "e")),
    ("I35ENB", (2, 35, "e")),
    ("US 31WBYPASS", (3, 31, "w bypass")),
    ("I 69 C", (2, 69, "c")),
    ("NY 9A", (4, 9, "a")),
    ("NY 9B", (4, 9, "b")),
    ("NY 5S", (4, 5, "s")),
    ("CA 178S", (4, 178, "s")),
    ("CR N1", None),
    ("N1", None),
])
def test_hpms_osm_refs_use_the_same_suffix_rules(ref, identity):
    assert hpms.route_of_ref(ref) == identity
