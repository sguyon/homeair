"""Road data (road_data.py): checked downloads, the retry, setup's roads and the route cut around the rounded place.
Every download is a fake: no test reaches the data host."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import road_data
import server
from config import load_config, set_roads
from road_data import ROUTE_CLIP_M, ROUTE_TOLERANCE_M, cache_files, read_outcome, retry_at_start, retry_due, route_stretch, setup_roads
from roads import RoadFile, entry, find_roads, haversine_m

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "roads"
CLIP = (FIXTURES / "nyc-2026-11-21-clip.json.gz").read_bytes()
CLIP_FILE = RoadFile.load(FIXTURES / "nyc-2026-11-21-clip.json.gz")
COORDS = [(p["lat"], p["lon"]) for p in json.loads((FIXTURES / "points.json").read_text(encoding="utf-8"))]
NAME = "nyc-2026-11.json.gz"
BASE = "https://data.example/roads"
NYC_BOX = [-74.359, 40.479, -73.671, 40.960]
NOW = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
WILLIAMSBURG = (40.7127, -73.959)


class Served:
    """A fake requests.get for the data host: index.json and the NYC clip under NAME; it records each file asked for."""

    def __init__(self):
        index = {"version": 1, "metros": [{"id": "nyc", "name": "New York", "level": 1, "country": "US", "box": NYC_BOX,
                                           "file": NAME, "bytes": len(CLIP), "sha256": hashlib.sha256(CLIP).hexdigest()}]}
        self.files = {"index.json": json.dumps(index).encode(), NAME: CLIP}
        self.calls = []

    def corrupt(self, name):
        data = bytearray(self.files[name])
        data[len(data) // 2] ^= 0xFF
        self.files[name] = bytes(data)

    def __call__(self, url, timeout=None, headers=None, stream=False):
        assert url.startswith(BASE + "/") and timeout and stream
        name = url.removeprefix(BASE + "/")
        self.calls.append(name)
        body = self.files.get(name) or b""
        return SimpleNamespace(status_code=200 if name in self.files else 404, close=lambda: None,
                               iter_content=lambda n: (body[i:i + n] for i in range(0, len(body), n)))


@pytest.fixture(autouse=True)
def data_host(monkeypatch):
    monkeypatch.delenv("ROAD_DATA_DIR", raising=False)
    monkeypatch.setattr(road_data, "ROADS_BASE", BASE)


@pytest.fixture
def served():
    return Served()


HAND_ROAD = """
# a road written by hand
[[roads]]
name = "Kent Avenue"  # the truck route
kind = "main road"
side = "W"
"""


def williamsburg_config(tmp_path, lat=WILLIAMSBURG[0], lon=WILLIAMSBURG[1], extra=""):
    path = tmp_path / "config.toml"
    path.write_text(f'[place]\nname = "Williamsburg"\nlat = {lat}\nlon = {lon}\ntimezone = "America/New_York"\n{extra}',
                    encoding="utf-8")
    return path


@pytest.fixture
def nyc_cache(tmp_path):
    folder = tmp_path / "road-data"
    folder.mkdir()
    (folder / NAME).write_bytes(CLIP)
    return SimpleNamespace(folder=folder, name=NAME, file=CLIP_FILE)


def vertices_within(key, place, metres):
    """The clip's points of a road (ramps and tunnels left out, as find_roads does) within `metres` of the place."""
    return {tuple(c) for f in CLIP_FILE.features if f["properties"].get("g") == key
            and not f["properties"].get("l") and not f["properties"].get("t")
            for c in f["geometry"]["coordinates"] if haversine_m(*place, c[1], c[0]) <= metres}


def test_bad_sha_writes_nothing_and_marks_pending(tmp_path, served):
    served.corrupt(NAME)
    res = setup_roads(williamsburg_config(tmp_path), None, get=served)
    assert res.status == "failed" and "[[roads]]" not in (tmp_path / "config.toml").read_text()
    assert res.lines == ["Roads: couldn't get the road data; Home Air on this computer tries again at its next start, at most once a day."]
    assert retry_due(tmp_path / "road-data", NOW)
    assert not (tmp_path / "road-data" / NAME).exists()   # nothing half-written
    assert (tmp_path / "road-data" / "pending").read_bytes() == b""   # nothing about the place in the marker


def test_a_hand_written_road_stays_in_place_and_a_failed_download_touches_nothing(tmp_path, served):
    path = williamsburg_config(tmp_path, extra=HAND_ROAD + "\n[sources]\nsmoke = true\n")
    before = path.read_bytes()
    served.corrupt(NAME)
    assert setup_roads(path, None, get=served).status == "failed"
    assert path.read_bytes() == before and not list(tmp_path.glob("config.toml.bak-*"))
    served.files[NAME] = CLIP
    assert setup_roads(path, None, get=served).status == "written"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(before.decode().rstrip("\n")) and text.count("[[roads]]") == 1 + text.count('found = "auto"')   # the hand block, then the roads found
    moved = text.replace("lat = 40.7127", "lat = 40.6720").replace("lon = -73.959", "lon = -73.9745")   # Park Slope
    path.write_text(moved, encoding="utf-8")
    assert setup_roads(path, WILLIAMSBURG, get=served).status in ("written", "none")
    assert path.read_text(encoding="utf-8").startswith(before.decode().replace("40.7127", "40.6720").replace("-73.959", "-73.9745").rstrip("\n"))
    assert "the BQE" not in path.read_text(encoding="utf-8")


def test_second_run_at_the_same_place_keeps_roads_byte_for_byte(tmp_path, served):
    res = setup_roads(williamsburg_config(tmp_path), None, get=served)
    assert res.status == "written" and served.calls == ["index.json", NAME]   # two GETs at setup
    assert res.lines == ['Roads: the BQE (highway, within 500 m, to the east). Turn one off in "Your home" › "Around you".']
    before = (tmp_path / "config.toml").read_bytes()
    assert setup_roads(tmp_path / "config.toml", WILLIAMSBURG, get=served).status == "kept"
    assert (tmp_path / "config.toml").read_bytes() == before
    assert served.calls == ["index.json", NAME]   # and none after


def test_route_is_cut_around_the_rounded_place(nyc_cache):
    drawn = 0
    for lat, lon in COORDS:
        r = (round(lat, 2), round(lon, 2))
        for f in find_roads(lat, lon, nyc_cache.file):
            lines = route_stretch(nyc_cache.folder, nyc_cache.name, f.key, *r)
            assert all(haversine_m(*r, la, lo) <= ROUTE_CLIP_M + ROUTE_TOLERANCE_M for line in lines for lo, la in line)
            assert vertices_within(f.key, r, ROUTE_CLIP_M - ROUTE_TOLERANCE_M) <= {tuple(p) for line in lines for p in line}
            assert all(len(line) >= 2 for line in lines)
            drawn += bool(lines)
    assert drawn > 40
    assert route_stretch(nyc_cache.folder, "nyc-2026-12.json.gz", "bqe", *WILLIAMSBURG) is None


@pytest.fixture
def client_with_auto_roads(client, tmp_path, monkeypatch):
    """The app on a home in Williamsburg with the roads setup finds there, and their file cached."""
    folder = tmp_path / "road-data"
    folder.mkdir()
    (folder / NAME).write_bytes(CLIP)
    monkeypatch.setenv("ROAD_DATA_DIR", str(folder))
    text = williamsburg_config(tmp_path).read_text(encoding="utf-8")
    text = set_roads(text, [entry(f, NAME, "US") for f in find_roads(*WILLIAMSBURG, CLIP_FILE)]) + HAND_ROAD
    before = server.CFG
    server.use_config(load_config({"CONFIG_TOML": text}))
    yield SimpleNamespace(get=client.get, folder=folder)
    server.use_config(before)


def test_the_route_is_drawn_from_the_cached_file_only(client_with_auto_roads):   # Review Focus 1
    c, folder = client_with_auto_roads, client_with_auto_roads.folder
    lines = c.get("/api/road/route?i=0").get_json()
    assert lines and all(len(p) == 2 for line in lines for p in line)
    assert c.get("/api/road/route?i=99").status_code == 404 and c.get("/api/road/route?i=-1").status_code == 404
    hand = len(server.CFG.roads) - 1   # a [[roads]] block written by hand: no route to draw
    assert server.CFG.roads[hand].name == "Kent Avenue" and c.get(f"/api/road/route?i={hand}").status_code == 404
    (folder / NAME).write_bytes(CLIP[: len(CLIP) // 2])   # corrupt
    assert c.get("/api/road/route?i=0").status_code == 404
    (folder / NAME).rename(folder / "nyc-2026-12.json.gz")   # only another month's file
    assert c.get("/api/road/route?i=0").status_code == 404
    assert c.get("/api/context").status_code == 200   # nothing raises


def test_missing_file_names_roads_without_drawing(client_with_auto_roads):   # Review Focus 1
    (client_with_auto_roads.folder / NAME).unlink()
    assert client_with_auto_roads.get("/api/road/route?i=0").status_code == 404
    assert client_with_auto_roads.get("/api/context").get_json()["roads"][0]["name"] == "the BQE"


def test_start_tries_once_a_day_and_fetches_only_a_missing_file(tmp_path, served):
    path = williamsburg_config(tmp_path)
    folder = tmp_path / "road-data"
    served.corrupt(NAME)
    setup_roads(path, None, get=served)
    assert retry_at_start(path, NOW, get=served).status == "failed"
    assert not retry_due(folder, NOW + timedelta(hours=23)) and retry_due(folder, NOW + timedelta(hours=24))
    served.files[NAME] = CLIP
    assert retry_at_start(path, NOW + timedelta(hours=24), get=served).status == "written"
    assert not (folder / "pending").exists() and retry_at_start(path, NOW + timedelta(hours=48), get=served) is None
    roads = load_config({"CONFIG_FILE": str(path)}).roads
    served.calls.clear()
    (folder / NAME).unlink()   # a restart without the file
    assert cache_files(folder, roads, get=served) == [] and served.calls == [NAME]
    assert cache_files(folder, roads, get=served) == [] and served.calls == [NAME]   # cached: no call
    (folder / NAME).unlink()
    (folder / "index.json").unlink()   # a new folder: the sha256 comes from index.json first
    (folder / "digests.json").unlink()
    assert cache_files(folder, roads, get=served) == [] and served.calls == [NAME, "index.json", NAME]
    (folder / NAME).unlink()
    served.files["index.json"] = json.dumps({"metros": []}).encode()   # an index that no longer names the file
    (folder / "index.json").unlink()
    (folder / "digests.json").unlink()  # no trusted history in a genuinely new folder
    assert cache_files(folder, roads, get=served) == [NAME] and not (folder / NAME).exists()


def test_a_download_stops_past_the_size_cap(tmp_path, served, monkeypatch):
    monkeypatch.setattr(road_data, "ROAD_FILE_MAX_BYTES", len(CLIP) - 1)
    with pytest.raises(road_data.RoadDataError):
        road_data.ensure_file(NAME, tmp_path, hashlib.sha256(CLIP).hexdigest(), get=served)
    monkeypatch.setattr(road_data, "ROAD_FILE_MAX_BYTES", len(CLIP))
    monkeypatch.setattr(road_data, "ROAD_JSON_MAX_BYTES", 1000)   # too large unzipped
    with pytest.raises(road_data.RoadDataError):
        road_data.ensure_file(NAME, tmp_path, hashlib.sha256(CLIP).hexdigest(), get=served)
    assert list(tmp_path.iterdir()) == []


def test_start_restores_the_same_dated_block_even_when_the_current_index_changed(tmp_path, served, monkeypatch):
    old = "us-block-2--37-20-2026-10.json.gz"
    today = "us-block-2--37-20-2026-11.json.gz"
    index = {"metros": [{"id": "us-block-2--37-20", "kind": "block", "level": "counted", "country": "US",
                         "box": [-74, 40, -72, 42], "file": old, "sha256": hashlib.sha256(CLIP).hexdigest()}]}
    served.files.update({"index.json": json.dumps(index).encode(), old: CLIP})
    path = williamsburg_config(tmp_path)
    assert setup_roads(path, None, get=served).status == "written"
    before = path.read_bytes()
    folder = tmp_path / "road-data"
    (folder / old).unlink()
    index["metros"][0]["file"] = today
    served.files.update({"index.json": json.dumps(index).encode(), today: CLIP})
    road_data.fetch_index(folder, get=served)  # a newer setup/index must retain October's trusted digest
    served.calls.clear()
    monkeypatch.setenv("CONFIG_FILE", str(path))
    monkeypatch.setenv("ROAD_DATA_DIR", str(folder))
    monkeypatch.setattr(server, "MODE", "home")
    monkeypatch.setattr(server, "CFG", load_config({"CONFIG_FILE": str(path)}))
    cache = road_data.cache_files
    monkeypatch.setattr(road_data, "cache_files", lambda folder, roads: cache(folder, roads, get=served))
    server.start_road_data(start=lambda fn: fn())
    assert served.calls == [old] and (folder / old).read_bytes() == CLIP
    assert not (folder / today).exists() and path.read_bytes() == before
    server.start_road_data(start=lambda fn: fn())
    assert served.calls == [old]  # a cached block needs no data calls
    (folder / old).unlink()
    served.corrupt(old)
    server.start_road_data(start=lambda fn: fn())
    assert served.calls == [old, old] and not (folder / old).exists()
    (folder / "index.json").unlink()  # cold file/index caches, but October's trusted digest must remain
    served.files[old] = CLIP
    server.start_road_data(start=lambda fn: fn())
    assert served.calls == [old, old, old]
    assert (folder / old).read_bytes() == CLIP
    assert not (folder / today).exists() and path.read_bytes() == before


def test_a_bad_block_download_leaves_pending_for_the_same_setup_selection(tmp_path, served):
    index = {"metros": [
        {"id": "block", "kind": "block", "level": 2, "country": "US", "box": [-74, 40, -72, 42],
         "file": NAME, "sha256": hashlib.sha256(CLIP).hexdigest()},
    ]}
    served.files["index.json"] = json.dumps(index).encode()
    served.corrupt(NAME)
    path = williamsburg_config(tmp_path)
    assert setup_roads(path, None, get=served).status == "failed"
    assert (tmp_path / "road-data" / "pending").exists()
    served.files[NAME] = CLIP
    assert retry_at_start(path, NOW, get=served).status == "written"
    assert served.calls == ["index.json", NAME, "index.json", NAME]
    assert load_config({"CONFIG_FILE": str(path)}).roads[0].data == NAME


def test_setup_records_its_outcome_and_the_page_reads_it(tmp_path, served, monkeypatch):   # review round 1, finding 3
    folder = tmp_path / "road-data"
    monkeypatch.setenv("CONFIG_FILE", str(tmp_path / "config.toml"))
    status = lambda: server.roads_status(load_config({"CONFIG_FILE": str(tmp_path / "config.toml")}))
    path = williamsburg_config(tmp_path)
    assert status() == "none" and not (folder / "outcome").exists()   # setup hasn't looked: never "not_built"
    (folder).mkdir()
    (folder / "index.json").write_text("not json")   # an unreadable index is no evidence either
    assert status() == "none"
    served.corrupt(NAME)
    assert setup_roads(path, None, get=served).status == "failed" and status() == "failed"
    assert read_outcome(folder) is None   # a failed try records no outcome, only the marker
    served.files[NAME] = CLIP
    assert setup_roads(path, None, get=served).status == "written"
    assert read_outcome(folder) == "found" and status() == "ok"
    monkeypatch.setattr(road_data, "find_roads", lambda *a: [])   # a metro with no main road within 1 km
    path.write_text(path.read_text(encoding="utf-8").replace("lat = 40.7127", "lat = 40.6720"), encoding="utf-8")
    assert setup_roads(path, WILLIAMSBURG, get=served).status == "none"
    assert read_outcome(folder) == "none" and status() == "none"
    far = williamsburg_config(tmp_path, lat=51.5, lon=-0.12)   # London: no data built
    assert setup_roads(far, None, get=served).status == "not_built"
    assert read_outcome(folder) == "not_built" and status() == "not_built"
    (folder / "outcome").write_text("somthing else")
    assert status() == "none"


def test_status_road_is_ok_with_an_auto_road_only(client_with_auto_roads):   # review round 1, finding 2
    assert client_with_auto_roads.get("/api/context").get_json()["status"]["road"] == "ok"


def test_a_damaged_cached_index_does_not_block_a_fresh_one(tmp_path):
    """A cached index without a list of metros adds no digests; the fresh index is still saved."""
    folder = tmp_path / "road-data"
    folder.mkdir()
    (folder / road_data.INDEX_NAME).write_text('{"metros": null}')
    fresh = {"metros": []}
    body = json.dumps(fresh).encode()
    reply = SimpleNamespace(status_code=200, iter_content=lambda n: [body], close=lambda: None)
    road_data.fetch_index(folder, get=lambda *a, **kw: reply)
    assert json.loads((folder / road_data.INDEX_NAME).read_text()) == fresh
