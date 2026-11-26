"""The roads build (scripts/roads/build.py) and roads.py's geometry, on a hand-written file: no osmium needed."""
import gzip
import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

import roads

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "roads" / "tiny.geojsonseq"
spec = importlib.util.spec_from_file_location("roads_build", ROOT / "scripts" / "roads" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
import hpms  # noqa: E402  (build.py put scripts/roads on the path)

SAMPLE = ROOT / "tests" / "fixtures" / "roads" / "hpms-sample.json"


def build_tiny(tmp_path, write=False):
    feats = build.read_features(FIXTURE)
    for f in feats:
        f["geometry"]["coordinates"] = build.simplify(f["geometry"]["coordinates"])
    names = build.NameTable.load(ROOT / "scripts" / "roads" / "names" / "nyc.toml")
    roads_ = build.group(feats, names)
    if not write:
        return roads_, feats
    meta = build.metro("nyc")
    entry = build.write_metro(meta, roads_, feats, tmp_path, date(2026, 12, 1), "2026-11-21")
    build.write_index([entry], tmp_path)
    return entry


def test_groups_follow_nearby_names_and_motorway_refs(tmp_path):
    roads_, feats = build_tiny(tmp_path)
    names = {r["name"] for r in roads_.values()}
    assert sum(r["name"] == "FDR Drive" for r in roads_.values()) == 2  # the fixture's pieces are apart
    assert {"Lemoine Avenue", "Schlosser Street"} <= names
    assert "the New Jersey Turnpike (Newark Bay Extension)" in names and "14th Street" in names
    assert "the Long Island Expressway" in names
    assert all(f["properties"]["g"] is None for f in feats if f["properties"].get("l"))


def test_keys_classes_and_pieces(tmp_path):
    roads_, feats = build_tiny(tmp_path)
    assert {"bqe", "fdr-drive", "lemoine-avenue", "14th-street", "long-island-expressway"} <= set(roads_)
    assert roads_["fdr-drive"]["class"] == "motorway" and roads_["fdr-drive"]["refs"] == ["FDR"]
    assert roads_["bqe"]["the"] is True and roads_["bqe"]["name"] == "the BQE"
    assert roads_["fdr-drive"]["the"] is False and roads_["fdr-drive"]["aadt"] is None
    by_g = {}
    for f in feats:
        by_g.setdefault(f["properties"]["g"], []).append(f["properties"])
    assert [p["c"] for p in by_g["fdr-drive"]] == ["motorway"]
    assert [p["c"] for p in by_g["fdr-drive-2"]] == ["primary"]
    assert any(p.get("t") for p in by_g["bqe"])               # the tunnel piece
    assert [p["c"] for p in by_g[None]] == ["motorway"]       # the ramp, kept for the map, in no group
    assert "queens-boulevard" in roads_                       # the unnamed NY 25 piece, by the table


def test_primary_roads_never_join_by_route_number():
    names = build.NameTable()
    feats = [{"properties": {"highway": "primary", "name": n, "ref": "NJ 67"},
              "geometry": {"type": "LineString", "coordinates": [[-73.97, 40.85], [-73.969, 40.851]]}}
             for n in ("Lemoine Avenue", "Schlosser Street")]
    assert sorted(r["name"] for r in build.group(feats, names).values()) == ["Lemoine Avenue", "Schlosser Street"]


def test_unnamed_pieces_with_no_named_road_join_by_their_first_route_number():
    seg = [[-73.97, 40.85], [-73.969, 40.851]]
    mk = lambda hw, **t: {"properties": {"highway": hw, **t}, "geometry": {"type": "LineString", "coordinates": seg}}
    feats = [mk("motorway", name="A Expressway", ref="I 9"), mk("motorway", name="A Expressway", ref="I 9;US 1"),
             mk("motorway", name="B Tunnel", ref="I 9;US 1"), mk("trunk", ref="I 5;US 9"), mk("trunk", ref="I 5")]
    out = build.group(feats, build.NameTable())
    assert [f["properties"]["g"] for f in feats][3:] == ["i-5"] * 2
    assert out["i-5"]["name"] == "I-5" and out["a-expressway"]["name"] == "the A Expressway"


def test_shared_highway_refs_leave_unnamed_and_named_roads_separate():
    seg = [[-73.97, 40.85], [-73.969, 40.851]]
    mk = lambda hw, **t: {"properties": {"highway": hw, **t}, "geometry": {"type": "LineString", "coordinates": seg}}
    feats = [mk("motorway", name="Brooklyn Express", ref="I 278"), mk("motorway", name="Gowanus Express", ref="I 278"),
             mk("motorway", ref="I 278"), mk("motorway", ref="I 9"), mk("motorway", ref="I 9;US 1")]
    out = build.group(feats, build.NameTable())
    assert [f["properties"]["g"] for f in feats] == [
        "brooklyn-express", "gowanus-express", "i-278", "i-9", "i-9"]
    assert out["brooklyn-express"]["name"] == "Brooklyn Express"
    assert out["gowanus-express"]["name"] == "Gowanus Express"
    assert out["i-278"]["name"] == "I-278" and out["i-9"]["name"] == "I-9" and len(out) == 4


def test_direction_and_lane_notes_do_not_split_a_road_but_a_service_road_stays_apart():
    seg = [[-73.97, 40.85], [-73.969, 40.851]]
    mk = lambda n: {"properties": {"highway": "motorway", "name": n}, "geometry": {"type": "LineString", "coordinates": seg}}
    feats = [mk(n) for n in ("Brooklyn–Queens Expressway East", "Brooklyn–Queens Expressway West", "Brooklyn–Queens Expressway (HOV)",
                             "Cross Bronx Expressway", "Cross Bronx Expressway Service Road")]
    table = build.NameTable.load(ROOT / "scripts" / "roads" / "names" / "nyc.toml")
    out = build.group(feats, table)
    assert [f["properties"]["g"] for f in feats] == ["bqe"] * 3 + ["cross-bronx-expressway", "cross-bronx-expressway-service-road"]
    assert build.base_name("Main Street (Express) Northbound") == "Main Street" and build.base_name("North") == "North"
    assert len(out) == 3


def test_nyc_rebuild_keeps_the_lower_level_name_at_hamilton_park():
    # These are the OSM name, ref and class on the motorway near the NYC pin.
    feats = [piece("NJ 139 Lower Level", "NJ 139", [[-74.05, 40.73], [-74.04, 40.73]], tunnel="yes")]
    nyc_names = build.NameTable.load(ROOT / "scripts" / "roads" / "names" / "nyc.toml")
    nyc_roads = build.group(feats, nyc_names)
    assert [road["name"] for road in nyc_roads.values()] == ["NJ 139 Lower Level"]
    assert [road["name"] for road in build.group(feats, build.NameTable()).values()] == ["NJ-139"]


def test_keys_that_collide_get_a_number_in_order_of_appearance():
    seg = [[-73.97, 40.85], [-73.969, 40.851]]
    mk = lambda n: {"properties": {"highway": "primary", "name": n}, "geometry": {"type": "LineString", "coordinates": seg}}
    out = build.group([mk("Main Street"), mk("Main-Street"), mk("Main  Street!")], build.NameTable())
    assert list(out) == ["main-street", "main-street-2", "main-street-3"] and out["main-street-2"]["name"] == "Main-Street"


def test_name_table_ref_restricts_a_name_that_is_also_a_street():
    table = build.NameTable.load(ROOT / "scripts" / "roads" / "names" / "nyc.toml")
    assert table.shown({"name": "14th Street", "ref": "I 78", "highway": "motorway"}) == ("the New Jersey Turnpike (Newark Bay Extension)"[4:], True)
    assert table.shown({"name": "14th Street", "highway": "primary"}) == ("14th Street", False)
    assert table.shown({"name": "Gowanus Expressway"}) == ("Gowanus Expressway", True)
    assert table.shown({"name": "Queensboro Bridge"}) == ("Queensboro Bridge", True)
    assert table.shown({"name": "Bridge Street"}) == ("Bridge Street", False)
    assert table.shown({"name": "George Washington Bridge Lower Level"}) == ("I-95 (GW Bridge)", False)
    assert table.shown({"ref": "NY 25"}) == ("Queens Boulevard", False)
    assert table.shown({"ref": "NJ 99"}) is None and table.shown({}) is None


def test_simplify_drops_points_within_tolerance_and_rounds():
    near = [[-73.9, 40.7], [-73.89995, 40.700010123456], [-73.8999, 40.7]]   # the middle point is about 1 m off the line
    assert build.simplify(near, 5) == [[-73.9, 40.7], [-73.8999, 40.7]]
    corner = [[-73.9, 40.7], [-73.9, 40.7005], [-73.8995, 40.7005]]            # a 55 m corner stays
    assert len(build.simplify(corner, 5)) == 3
    assert all(len(str(c).split(".")[-1]) <= 5 for p in build.simplify([[-73.123456789, 40.987654321], [-73.1, 41.0]]) for c in p)


def test_metro_nyc():
    assert build.metro("nyc") == {
        "id": "nyc", "name": "New York", "level": 1, "country": "US", "box": [-74.359, 40.479, -73.671, 40.960],
        "source_url": "https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf", "hpms_states": ["NY", "NJ"],
        "speeds": "nyc-dot"}
    with pytest.raises(ValueError):
        build.metro("nowhere")


def test_metro_file_and_index(tmp_path):
    entry = build_tiny(tmp_path, write=True)
    data = json.loads(gzip.decompress((tmp_path / entry["file"]).read_bytes()))
    assert data["licence"] == "ODbL-1.0" and data["credit"] == "© OpenStreetMap contributors"
    assert entry["sha256"] == hashlib.sha256((tmp_path / entry["file"]).read_bytes()).hexdigest()
    assert entry["bytes"] == (tmp_path / entry["file"]).stat().st_size and entry["osm_date"] == "2026-11-21"
    assert all(len(str(c).split(".")[-1]) <= 5 for f in data["features"] for pt in f["geometry"]["coordinates"] for c in pt)
    assert entry["file"] == "nyc-2026-12.json.gz"
    assert data["metro"] == "nyc" and data["level"] == 1 and data["built"] == "2026-12-01"
    assert data["source"] == "https://github.com/sguyon/homeair/tree/main/scripts/roads"
    assert set(data["roads"]["bqe"]) == {"name", "the", "class", "refs", "aadt", "trucks_pct"}
    assert {k for f in data["features"] for k in f["properties"]} <= {"g", "c", "t", "l"}
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["version"] == 1 and index["licence"] == "ODbL-1.0" and index["source"] == data["source"]
    assert set(index["metros"][0]) == {"id", "name", "level", "country", "box", "file", "osm_date", "built", "bytes", "sha256"}


def test_the_file_is_the_same_every_time(tmp_path):
    a = build_tiny(tmp_path / "a", write=True)
    b = build_tiny(tmp_path / "b", write=True)
    assert a["sha256"] == b["sha256"]


def test_read_features_accepts_record_separators(tmp_path):
    src = tmp_path / "x.geojsonseq"
    line = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    point = '{"type":"Feature","geometry":{"type":"Point","coordinates":[0,0]},"properties":{}}'
    src.write_text(f"\x1e{line}\n\x1e{point}\n", encoding="utf-8")
    feats = build.read_features(src)
    assert len(feats) == 1 and set(feats[0]["properties"]) <= set(build.KEEP_TAGS)


def test_clip_keeps_pieces_with_a_vertex_near_a_point():
    feats = build.read_features(FIXTURE)
    kept = build.clip_features(feats, [(40.7127, -73.9590)], 100)
    assert [f["properties"].get("name") for f in kept] == ["Brooklyn–Queens Expressway"]


def test_geometry_helpers():
    to_xy = roads.local_xy(40.7, -74.0)
    assert to_xy(-74.0, 40.7) == (0.0, 0.0)
    x, y = to_xy(-74.0, 40.701)
    assert x == 0 and y == pytest.approx(111.2, abs=0.5)
    assert to_xy(-73.999, 40.7)[0] == pytest.approx(84.3, abs=0.5)
    d, near = roads.seg_distance((5, 5), (0, 0), (10, 0))
    assert d == 5 and near == (5, 0)
    assert roads.seg_distance((-3, 4), (0, 0), (10, 0)) == (5.0, (0.0, 0.0))
    assert roads.seg_distance((3, 4), (0, 0), (0, 0)) == (5.0, (0.0, 0.0))
    assert [roads.bearing_deg(*v) for v in ((0, 1), (1, 0), (0, -1), (-1, 0))] == [0, 90, 180, 270]
    assert roads.haversine_m(40.7, -74.0, 40.701, -74.0) == pytest.approx(111.2, abs=0.5)
    assert roads.haversine_m(40.7, -74.0, 40.7, -74.0) == 0


# ---- HPMS counts ------------------------------------------------------------------------------------------

def hpms_segments():
    return json.loads(SAMPLE.read_text())["features"]


def piece(name, ref, coords, hw="motorway", **tags):
    return {"properties": {"highway": hw, "name": name, "ref": ref, **tags} if name or ref else {"highway": hw},
            "geometry": {"type": "LineString", "coordinates": coords}}


def build_tiny_with_crossbronx():
    """The Cross Bronx motorway (ref "I 95") along the sample's geometry, with the Gowanus, 4th Avenue and a ramp."""
    segs = hpms_segments()
    line = lambda i: segs[i]["geometry"]["coordinates"]
    feats = [piece("Cross Bronx Expressway", "I 95;I 295;US 1", line(0)),
             piece("Gowanus Expressway", "I 278", line(3)),
             piece("4th Avenue", None, line(4), hw="primary"),
             piece(None, None, line(1), hw="motorway_link")]
    roads_ = build.group(feats, build.NameTable.load(ROOT / "scripts" / "roads" / "names" / "nyc.toml"))
    return roads_, feats


def test_hpms_joins_route_and_skips_ramps(tmp_path):
    roads_, feats = build_tiny_with_crossbronx()
    n = hpms.join(roads_, feats, hpms_segments())
    cbx = next(r for r in roads_.values() if r["name"] == "the Cross Bronx Expressway")
    assert cbx["aadt"] == 111822 and cbx["trucks_pct"] == 17 and n >= 1   # not the 8,130 or 17,600 ramps beside it
    gow = next(r for r in roads_.values() if r["name"] == "the Gowanus Expressway")
    assert gow["aadt"] == 147008 and gow["trucks_pct"] == 11 and n == 3
    assert roads_["4th-avenue"]["aadt"] == 15189 and roads_["4th-avenue"]["trucks_pct"] == 5   # by name: "4TH AVE" = "4th Avenue"


def test_hpms_never_gives_a_road_the_count_of_another_road_with_the_same_ref():
    segs = hpms_segments()
    far = [[c[0] + 0.01, c[1]] for c in segs[0]["geometry"]["coordinates"]]   # the same I 95 count, 800 m away
    feats = [piece("Some Other Expressway", "I 95", far), piece(None, "I 95", far)]
    roads_ = build.group(feats, build.NameTable())
    assert hpms.join(roads_, feats, segs) == 0 and all(r["aadt"] is None for r in roads_.values())
    near = [[c[0], c[1] + 0.0002] for c in segs[0]["geometry"]["coordinates"]]   # 22 m off the segment: joined
    feats = [piece("Some Other Expressway", "I 95", near)]
    roads_ = build.group(feats, build.NameTable())
    assert hpms.join(roads_, feats, segs) == 1 and next(iter(roads_.values()))["aadt"] == 111822
    roads_ = build.group(feats, build.NameTable())
    assert hpms.join(roads_, feats, segs, within_m=10) == 0


def test_hpms_the_segment_along_most_of_the_road_wins_and_a_ramp_row_never_counts():
    seg = lambda name, num, sign, aadt, coords: {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                                                 "properties": {"route_name": name, "route_number": num, "route_signing": sign,
                                                                "aadt": aadt, "aadt_combination": None, "aadt_single_unit": None}}
    road = [[-73.90, 40.80], [-73.89, 40.80]]
    feats = [piece("Main Expressway", "I 90", road)]
    roads_ = build.group(feats, build.NameTable())
    short = seg("I90", 90, 2, 1000, [[-73.90, 40.80], [-73.8985, 40.80]])
    long_ = seg("I90", 90, 2, 2000, [[-73.8985, 40.80], [-73.89, 40.80]])
    ramp = seg("I90 EB to Elm St", None, 1, 3000, road)
    assert hpms.join(roads_, feats, [short, ramp, long_]) == 1
    assert next(iter(roads_.values()))["aadt"] == 2000 and next(iter(roads_.values()))["trucks_pct"] is None


def test_hpms_route_and_name_helpers():
    assert [hpms.route_of_ref(r) for r in ("I 95", "US 1", "NY 25", "NJTP", "I-278")] == [(2, 95), (3, 1), (4, 25), None, (2, 278)]
    assert hpms.norm_name("4TH AVE") == hpms.norm_name("the 4th Avenue") == "4th avenue"
    assert hpms.norm_name("Queens Expy") == hpms.norm_name("Queens Expressway") and hpms.norm_name("Park St") == "park street"
    assert hpms.norm_name(None) == ""


def test_add_counts_prints_a_rate_per_class_and_skips_other_countries(tmp_path, monkeypatch):
    roads_, feats = build_tiny_with_crossbronx()
    monkeypatch.setattr(hpms, "fetch", lambda *args: hpms_segments())
    meta = {"country": "US", "box": [-74.4, 40.5, -73.6, 40.9], "hpms_states": ["NY"]}
    assert build.add_counts(meta, roads_, feats, tmp_path, Path("HPMS2024.zip")) == ["motorway 2 of 2 joined", "primary 1 of 1 joined"]
    assert build.add_counts({**meta, "country": "FR"}, roads_, feats, tmp_path, Path("HPMS2024.zip")) == []
