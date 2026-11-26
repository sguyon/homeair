"""Frozen US clips retain whole-metro names and match national clipping."""
import gzip
import hashlib
import importlib.util
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from roads import RoadFile

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def freezer():
    spec = importlib.util.spec_from_file_location("freeze_us_clip", ROOT / "scripts/roads/freeze_us_clip.py")
    freeze = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freeze)
    return freeze


@pytest.fixture
def frozen(tmp_path, monkeypatch, freezer):
    freeze = freezer
    metros = [{"cbsa": cbsa, "rank": rank, "name": cbsa, "box": [x - .1, -.1, x + .1, .1]}
              for rank, (cbsa, x) in enumerate((("11111", 0), ("22222", 1)), 1)]
    monkeypatch.setattr(freeze.build, "load_us_regions", lambda: (metros, {}))
    monkeypatch.setattr(freeze.build, "osm_date_of", lambda pbf: "2026-11-24")
    names = tmp_path / "tables" / "names"
    names.mkdir(parents=True)
    for m in metros:
        (names / f"us-{m['cbsa']}.toml").write_text(
            '[[rename]]\nname = "Side Street"\nshown = "Reviewed Street"\n', encoding="utf-8")
    monkeypatch.setattr(freeze.build, "HERE", names.parent)

    def extract_export(pbf, region, work):
        x = 0 if region["cbsa"] == "11111" else 1

        def feature(name, coords, highway="motorway"):
            return {"type": "Feature", "properties": {"highway": highway, "name": name},
                    "geometry": {"type": "LineString", "coordinates": [[x + lon, lat] for lon, lat in coords]}}

        # The remote piece joins across a 111 m gap and supplies most of the
        # road length. Clipping before grouping would pick "Harbor Freeway".
        features = [feature("Harbor Freeway", [[.0005, 0], [.002, 0]]),
                    feature("HARBOR FREEWAY", [[.003, 0], [.03, 0]]),
                    feature("Side Street", [[0, .0005], [.001, .0005]], "primary"),
                    feature("Far Road", [[.05, 0], [.06, 0]], "primary")]
        path = work / "synthetic.geojsonseq"
        path.write_text("\n".join(json.dumps(f) for f in features), encoding="utf-8")
        return path

    monkeypatch.setattr(freeze, "extract_export", extract_export)
    points = tmp_path / "us-points.json"
    points.write_text(json.dumps({"points": [{"cbsa": "11111", "lat": 0, "lon": 0},
                                            {"cbsa": "22222", "lat": 0, "lon": 1}]}), encoding="utf-8")
    out = tmp_path / "clip.json.gz"
    assert freeze.main([str(tmp_path / "input.pbf"), "--points", str(points), "--out", str(out),
                        "--clip-m", "200", "--work", str(tmp_path / "work")]) == 0
    return freeze, points, out, json.loads(gzip.decompress(out.read_bytes()))


def test_clip_names_are_chosen_before_distant_features_are_dropped(frozen):
    _, _, _, doc = frozen
    assert sorted(r["name"] for r in doc["roads"].values()) == [
        "HARBOR FREEWAY", "HARBOR FREEWAY", "Reviewed Street", "Reviewed Street"]
    assert len(doc["features"]) == 4
    assert all(max(lon % 1 for lon, lat in f["geometry"]["coordinates"]) <= .0021
               for f in doc["features"])


def test_clip_namespaces_group_keys_and_loads_as_a_road_file(frozen):
    _, _, out, doc = frozen
    data = RoadFile.load(out)
    assert len(data.roads) == 4
    assert {f["properties"]["g"] for f in data.features} == set(data.roads)
    assert {key.split(":")[0] for key in data.roads} == {"us-11111", "us-22222"}
    assert doc["licence"] == "ODbL-1.0"
    assert doc["credit"] == "© OpenStreetMap contributors"
    assert doc["osm_date"] == "2026-11-24"
    assert "placeholder" not in doc


def test_clip_bytes_are_deterministic_across_output_and_work_paths(frozen, tmp_path):
    freeze, points, out, _ = frozen
    again = tmp_path / "again.json.gz"
    assert freeze.main([str(tmp_path / "input.pbf"), "--points", str(points), "--out", str(again),
                        "--clip-m", "200"]) == 0
    assert again.read_bytes() == out.read_bytes()
    assert out.read_bytes()[4:8] == b"\x00\x00\x00\x00"


@pytest.mark.skipif(not shutil.which("osmium"), reason="requires osmium-tool")
def test_way_with_no_node_inside_data_box_matches_national_build(freezer, tmp_path, monkeypatch):
    build = freezer.build
    metros = [{"cbsa": "11111", "rank": 1, "name": "Test Metro", "box": [-.005, -.005, .005, .005]}]
    monkeypatch.setattr(build, "load_us_regions", lambda: (metros, {}))
    monkeypatch.setattr(build.hpms, "load_states", lambda: {})
    monkeypatch.setattr(build.check_us, "preflight", lambda *args: None)
    monkeypatch.setattr(build.check_us, "check_build", lambda *args: None)
    monkeypatch.setattr(build, "HERE", tmp_path)
    names = tmp_path / "names"
    names.mkdir()
    (names / "us-11111.toml").write_text('[[rename]]\nname = "Crossing"\nshown = "Reviewed Crossing"\n')

    # The motorway crosses the roughly ±.023° data box. Neither node is
    # inside it, but both are close enough to be selected by the padded cut.
    source = tmp_path / "input.osm"
    source.write_text('''<osm version="0.6">
      <node id="1" lat="0" lon="-0.035"/>
      <node id="2" lat="0" lon="0.035"/>
      <way id="1"><nd ref="1"/><nd ref="2"/>
        <tag k="highway" v="motorway"/><tag k="name" v="Crossing"/>
      </way>
    </osm>''')
    pbf = tmp_path / "input.osm.pbf"
    build._osmium("cat", str(source), "--output-header=osmosis_replication_timestamp=2026-11-24T00:00:00Z",
                  "-o", str(pbf))
    points = tmp_path / "points.json"
    points.write_text(json.dumps({"points": [{"cbsa": "11111", "lat": 0, "lon": 0}]}))
    out = tmp_path / "out"
    hpms_file = ROOT / "tests/fixtures/roads/hpms2024.zip"
    monkeypatch.setattr(build.hpms, "HPMS_FILE_BYTES", hpms_file.stat().st_size)
    monkeypatch.setattr(build.hpms, "HPMS_FILE_SHA256", hashlib.sha256(hpms_file.read_bytes()).hexdigest())
    region, = build.build_us(pbf, out, tmp_path / "national-work", date(2026, 11, 24),
                            hpms_file=hpms_file)["metros"]
    national = RoadFile.load(out / region["file"])
    clip = tmp_path / "clip.json.gz"
    freezer.freeze(pbf, points, clip, tmp_path / "clip-work")
    frozen = RoadFile.load(clip)

    road, = national.roads.values()
    feature, = national.features
    assert road["name"] == "Reviewed Crossing"
    west, _, east, _ = region["data_box"]
    assert -.035 < west < east < .035
    assert feature["geometry"]["coordinates"] == [[round(west, 5), 0], [round(east, 5), 0]]
    prefix = "us-11111:"
    assert frozen.roads == {prefix + k: v for k, v in national.roads.items()}
    assert frozen.features == [{**feature, "properties": {**feature["properties"],
                                                          "g": prefix + feature["properties"]["g"]}}]
