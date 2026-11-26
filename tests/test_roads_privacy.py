"""Road privacy at the real output boundaries; public NYC points, never a home's data.

The hand-set [road] is deliberately absent: its owner-drawn arc is allowed in
degrees. These arc assertions apply to setup's [[roads]] only.
"""
import json
import re
import tomllib
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import road_data
import server
import sources
from config import ConfigError, load_config, online_roads, set_roads
from roads import SECTORS, RoadFile, entry, find_roads, haversine_m, sectors
from scripts import setup

FIXTURES = Path(__file__).parent / "fixtures" / "roads"
DATA_FILE = "nyc-2026-11-21-clip.json.gz"
POINTS = [(p["label"], p["lat"], p["lon"]) for p in json.loads((FIXTURES / "points.json").read_text())]
BANDS = {"within 200 m", "within 500 m", "500 m to 1 km", "no main road within 1 km"}
PINNED = {  # Re-measured 24 Nov after name-only grouping; names / bands / sides / sectors.
    # Regression detectors, not privacy rules. Review any change before re-pinning.
    "Williamsburg": (60, 45, 3, 0), "Sunset Park": (17, 17, 10, 2),
    "East Tremont": (100, 49, 41, 7), "Rego Park": (27, 27, 14, 3),
    "Stuyvesant Town": (14, 14, 10, 1), "Yorkville": (109, 47, 2, 2),
    "Boerum Hill": (6, 6, 6, 4), "Park Slope": (62, 62, 62, 62),
    "Great Kills": (114, 114, 114, 114), "Westerleigh": (35, 35, 35, 26),
    "Jersey City": (49, 16, 1, 1), "Midtown East": (2, 2, 1, 1),
    "Fort Lee": (3, 3, 1, 1), "Long Island City": (5, 5, 1, 1),
    "Kew Gardens": (3, 3, 2, 1), "Hunts Point": (121, 108, 46, 6),
}
PUBLISHED_PINNED = {  # Re-measured 24 Nov after name-only grouping, off rows/status included.
    # Regression detectors, not privacy rules. Review any change before re-pinning.
    # Each tuple is names / + bands / + sides / + online sectors for ALL listed roads.
    'Williamsburg': (8, 3, 0, 0),
    'Sunset Park': (17, 17, 10, 2),
    'East Tremont': (44, 19, 16, 3),
    'Rego Park': (15, 15, 6, 1),
    'Stuyvesant Town': (8, 2, 2, 1),
    'Yorkville': (5, 3, 2, 1),
    'Boerum Hill': (3, 2, 1, 1),
    'Park Slope': (6, 5, 4, 4),
    'Great Kills': (105, 105, 105, 105),
    'Westerleigh': (35, 35, 35, 26),
    'Jersey City': (42, 11, 1, 1),
    'Midtown East': (1, 1, 1, 1),
    'Fort Lee': (3, 3, 1, 1),
    'Long Island City': (2, 2, 1, 1),
    'Kew Gardens': (3, 3, 2, 1),
    'Hunts Point': (46, 38, 26, 6),
}
FIELDS_PINNED = {  # Re-measured 24 Nov after name-only grouping, actual published fields.
    # Regression detectors, not privacy rules. Review any change before re-pinning.
    'Williamsburg': 0,
    'Sunset Park': 10,
    'East Tremont': 16,
    'Rego Park': 6,
    'Stuyvesant Town': 2,
    'Yorkville': 2,
    'Boerum Hill': 1,
    'Park Slope': 4,
    'Great Kills': 105,
    'Westerleigh': 35,
    'Jersey City': 1,
    'Midtown East': 1,
    'Fort Lee': 1,
    'Long Island City': 1,
    'Kew Gardens': 2,
    'Hunts Point': 26,
}
NOW = datetime(2026, 11, 24, 13, tzinfo=ZoneInfo("America/New_York"))
# timing fields: they follow the wind and the hour (toward_home is the same test as reaching, at any hour, on the
# arc the page already acts on: sectors outside this computer)
DYNAMIC = {"reaching", "strength", "why", "until", "until_why", "wind_after", "toward_home", "light_wind", "reach_m",
           "far_toward", "busy_source", "horizon_h"}
PRIVATE_ADDRESS = "123 Privacy Test Lane, Apartment 4B"  # invented street number and apartment, never a real home


@pytest.fixture(scope="module")
def picks():
    data = RoadFile.load(FIXTURES / DATA_FILE)
    return lru_cache(maxsize=None)(lambda lat, lon: find_roads(lat, lon, data))


def config_text(lat, lon, found=()):
    text = (f'[place]\nname = "Privacy fixture"\nlat = {lat}\nlon = {lon}\n'
            'timezone = "America/New_York"\nindoor = "home"\noutdoor = "outdoor"\n'
            '[sources]\nsmoke = false\npollen = false\nnearby = false\n')
    return set_roads(text, [entry(f, DATA_FILE, "US") for f in found])


@pytest.fixture
def home_at(monkeypatch, canned, picks, tmp_path):
    old = server.CFG
    monkeypatch.setattr(server, "MODE", "live")
    monkeypatch.setattr(sources, "local_now", lambda: NOW)
    rows = [{"locationId": i, "locationType": kind, "locationName": kind,
             "timestamp": NOW.isoformat(), "rco2": 600, "pm02": 4, "rhum": 45, "atmp": 21,
             "address": PRIVATE_ADDRESS}
            for i, kind in enumerate(("indoor", "outdoor"), 1)]
    monkeypatch.setattr(sources, "home_rows", lambda **kw: (rows, {"state": "fresh", "age": 0}))
    monkeypatch.setattr(sources, "current_rows", lambda **kw: (rows, {"state": "fresh", "age": 0}))
    monkeypatch.setattr(sources, "history_points", lambda *a, **kw: [])
    monkeypatch.setattr(sources, "api_get", lambda *a, **kw: [])
    # Neither inference nor output tests may reach an upstream or the real store.
    monkeypatch.setattr(sources.requests, "get", lambda *a, **kw: pytest.fail("unexpected network request"))
    monkeypatch.setenv("ROAD_DATA_DIR", str(FIXTURES))
    monkeypatch.setattr(setup, "saved_keys", lambda root: {"AIRGRADIENT_TOKEN": "invented-test-token"})
    monkeypatch.setattr(setup, "env_key", lambda name, keys: keys.get(name, ""))
    sent = {}

    def capture(args, **kw):
        sent[args[3]] = kw["input"]

    monkeypatch.setattr(setup.subprocess, "run", capture)
    server.app.config["TESTING"] = True
    client = server.app.test_client()

    def at(lat, lon, roads=True):
        for row in rows:
            row.update(latitude=lat, longitude=lon)
        text = config_text(lat, lon, picks(lat, lon) if roads else ())
        server.use_config(load_config({"CONFIG_TOML": text}))
        page = client.get("/")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        app = json.loads(re.search(r"window\.APP = (.*?);</script>", html).group(1))
        context = client.get("/api/context")
        agent = client.get("/api/agent/now")
        history = client.get("/api/agent/history?hours=1")
        mcp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "home_air_now", "arguments": {}}})
        assert context.status_code == agent.status_code == history.status_code == mcp.status_code == 200
        assert "error" not in agent.get_json() and "error" not in history.get_json()
        assert mcp.get_json()["result"]["isError"] is False
        path = tmp_path / "config.toml"
        path.write_text(text)
        sent.clear()
        assert setup.railway("privacy-test-service", tmp_path) == 0
        return {"page": html, "APP": app, "context": context.get_json(), "agent": agent.get_json(),
                "history": history.get_json(), "mcp": mcp.get_json(), "railway": sent["CONFIG_TOML"]}

    yield at
    server.use_config(old)


def strip_phrases(text):
    for phrase in sorted(BANDS, key=len, reverse=True):
        text = text.replace(phrase, "")
    return text


@pytest.mark.parametrize("label,lat,lon", POINTS)
def test_nothing_sent_holds_a_distance_an_arc_or_a_precise_coordinate(label, lat, lon, home_at):
    # Literal home coordinates must not pin a location finer than one grid cell:
    # 0.001 degrees, about 111 m N/S by 84 m E/W here. The stricter existing
    # regex forbids road-added coordinates at 3+ decimal places at EVERY boundary.
    # Inference from allowed names/bands/sides/sectors is measured separately.
    shown, base = home_at(lat, lon), home_at(lat, lon, roads=False)
    for boundary, value in shown.items():
        text = value if isinstance(value, str) else json.dumps(value)
        plain = base[boundary] if isinstance(base[boundary], str) else json.dumps(base[boundary])
        for rx in (r"\b\d+(?:\.\d+)?\s?(?:m|km|metres|meters)\b", r"-?\d{1,3}\.\d{3,}"):
            new = set(re.findall(rx, strip_phrases(text))) - set(re.findall(rx, plain))
            assert not new, (label, boundary, new)
        assert not re.search(r'"?toward(_calm)?"?\s*[:=]\s*\[\s*\d', text), (label, boundary)
        assert PRIVATE_ADDRESS not in text, (label, boundary)
        for address_part in PRIVATE_ADDRESS.split(", "):
            assert address_part not in text, (label, boundary, address_part)
        for coordinate in (lat, lon):
            if coordinate != round(coordinate, 2):
                assert str(coordinate) not in text, (label, boundary, coordinate)
    online = tomllib.loads(shown["railway"])
    assert (online["place"]["lat"], online["place"]["lon"]) == (round(lat, 2), round(lon, 2))
    for place in (shown["APP"]["place"], online["place"],
                  base["APP"]["place"], tomllib.loads(base["railway"])["place"]):
        # 2-decimal home coordinates are coarser than the 0.001-degree grid cell;
        # check the actual values, including the no-roads baseline's place fields.
        assert (place["lat"], place["lon"]) == (round(lat, 2), round(lon, 2)), label
    for r in online.get("roads", []):
        for key in ("toward", "toward_calm"):
            if key in r:
                assert r[key] and set(r[key]) <= set(SECTORS), (label, key)


@pytest.mark.parametrize("value", ['[74.5, 162]', '[-1, 162]', '["E", 162]', '["ENE"]', '[]', '"E"', '[true, false]'])
@pytest.mark.parametrize("key", ["toward", "toward_calm"])
def test_online_arcs_fail_closed(key, value):
    with pytest.raises(ConfigError):
        online_roads(f'[[roads]]\nname = "Test road"\n{key} = {value}\n')


def cell_grid(lat, lon):
    # 121 points, 0.001 degrees apart: about 111 m north/south and 84 m east/west
    # at these NYC-area latitudes. Counts sample inference; they do not measure an
    # equivalence region's area (the exact fixture point may lie between samples).
    return [(round(round(lat, 2) - .005 + i * .001, 6),
             round(round(lon, 2) - .005 + j * .001, 6)) for i in range(11) for j in range(11)]


def signature(found, level, published=False):
    rows = []
    for f in found:
        if not published and not f.followed:
            continue
        row = (f.name, f.followed) if published else (f.name,)
        if level >= 1:
            row += (f.band,)
        if level >= 2:
            row += (f.side,)
        if level >= 3:
            row += (tuple(sectors(f.toward)) if f.toward else (), tuple(sectors(f.toward_calm)))
        rows.append(row)
    # An off road is still in APP.roads, and makes roads_status "ok", not "none".
    return ("ok" if found else "none", tuple(rows)) if published else tuple(rows)


@pytest.mark.parametrize("label,lat,lon", POINTS)
def test_measured_inference_counts_are_pinned(label, lat, lon, picks):
    home = picks(lat, lon)
    grid = [picks(*p) for p in cell_grid(lat, lon)]
    counts = tuple(sum(signature(g, level) == signature(home, level) for g in grid) for level in range(4))
    name = next(name for name in PINNED if label.startswith(name))
    assert counts == PINNED[name], (label, counts)
    published = tuple(sum(signature(g, level, True) == signature(home, level, True) for g in grid)
                      for level in range(4))
    assert published == PUBLISHED_PINNED[name], (label, published)


def page_fields(shown):
    """Every road field from the actual APP, context, agent and MCP outputs.

    Timing fields and an action's reaching subset may change with wind/time.
    Every action road must match the public inventory; names and all remaining
    inventory fields (including off rows, counts, keys and `several`) stay in the comparison.
    """
    def stable(rows, sort=False):
        rows = [{k: v for k, v in r.items() if k not in DYNAMIC} for r in rows]
        return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)) if sort else rows

    def action_roads(answer):
        public = [{k: r.get(k) for k in ("name", "band", "side")} for r in shown["APP"].get("roads", [])]
        rows = stable([r for action in answer["read"]["actions"] for r in action.get("roads", [])])
        assert all(row in public for row in rows), rows
        # Normalize only membership, which is the same timing fact as context.reaching.
        return stable(public, sort=True)

    app = shown["APP"]
    return {"APP.roads": stable(app.get("roads", [])), "APP.roads_off": stable(app.get("roads_off", [])),
            "APP.roads_status": app.get("roads_status"),
            "context.roads": stable(shown["context"]["roads"], sort=True),
            "agent.roads": action_roads(shown["agent"]),
            "mcp.roads": action_roads(shown["mcp"]["result"]["structuredContent"])}


def differences(a, b, path=""):
    """Compact failure evidence: identify the published fields that narrow the cell."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [p for key in a.keys() | b.keys()
                for p in differences(a.get(key), b.get(key), f"{path}.{key}" if path else key)]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in differences(x, y, f"{path}[{i}]")]
    return [] if a == b else [(path, a, b)]


@pytest.mark.parametrize("label,lat,lon", POINTS)
def test_published_field_inference_counts_are_pinned(label, lat, lon, picks, home_at):
    # All 121 grid points count; off rows and roads_status are published too.
    grid = cell_grid(lat, lon)
    target = signature(picks(lat, lon), 2, published=True)
    fields = page_fields(home_at(lat, lon))
    same_sides = [p for p in grid if signature(picks(*p), 2, published=True) == target]
    # Other points already differ in allowed fields; only these can narrow further.
    candidates = [(p, page_fields(home_at(*p))) for p in same_sides]
    matches = sum(other == fields for _, other in candidates)
    changes = [(p, differences(fields, other)) for p, other in candidates if other != fields]
    name = next(name for name in PINNED if label.startswith(name))
    assert matches >= len(same_sides), (label, matches, len(same_sides), changes)
    assert matches == FIELDS_PINNED[name], (label, matches, len(same_sides), changes)


def test_route_endpoint_is_cut_around_rounded_place(home_at, picks):
    assert road_data.ROUTE_CLIP_M == 3000
    lat, lon = POINTS[0][1:]
    home_at(lat, lon)
    client = server.app.test_client()
    rounded = (round(lat, 2), round(lon, 2))
    for i, f in enumerate(picks(lat, lon)):
        response = client.get(f"/api/road/route?i={i}")
        assert response.status_code == 200
        lines = response.get_json()
        assert lines == road_data.route_stretch(FIXTURES, DATA_FILE, f.key, *rounded)
        assert lines
        assert all(haversine_m(*rounded, la, lo) <= 3001 for line in lines for lo, la in line)
    # Same road and same rounded cell must return byte-identical geometry.
    before = client.get("/api/road/route?i=0").data
    home_at(lat + .0001, lon + .0001)
    assert picks(lat + .0001, lon + .0001)[0].key == picks(lat, lon)[0].key
    assert client.get("/api/road/route?i=0").data == before
