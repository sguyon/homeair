"""The picking rule (roads.py), pinned at 16 public points of New York on the 21 Nov 2026 OpenStreetMap file."""
import json
from pathlib import Path

import pytest

from roads import SECTORS, RoadFile, arc, entry, find_roads, reaches, sectors, side_name

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "roads"
POINTS = [(p["label"], p["lat"], p["lon"]) for p in json.loads((FIXTURES / "points.json").read_text(encoding="utf-8"))]
COORDS = [(lat, lon) for _, lat, lon in POINTS]
W200, W500, FAR = "within 200 m", "within 500 m", "500 m to 1 km"

FIXED = {  # (name, band, side) of the followed roads, in selection order
    "Williamsburg, Havemeyer St": [("the BQE", W500, "E")],
    "Sunset Park, 4th Ave & 40th St": [("the Gowanus Expressway", W500, "NW"), ("4th Avenue", W200, "SE")],
    "East Tremont, Bronx": [("the Cross Bronx Expressway", W500, "N")],
    "Rego Park, Queens Blvd & 63rd Dr": [("Queens Boulevard", W200, "NE"), ("the Long Island Expressway", FAR, "N")],
    "Stuyvesant Town": [("FDR Drive", W500, "E"), ("Avenue C", W200, "E")],
    "Yorkville, East End Ave": [("FDR Drive", W500, "NE")],
    "Boerum Hill, Atlantic Ave & Bond St": [("Atlantic Avenue", W200, "NE")],
    "Park Slope, 8th Ave & 4th St": [],
    "Great Kills, Staten Island": [],
    "Westerleigh, Staten Island": [("the Staten Island Expressway", FAR, "S")],
    "Jersey City, Hamilton Park": [("the New Jersey Turnpike (Newark Bay Extension)", W500, "N"), ("NJ 139 Lower Level", FAR, "NW"),
                                   ("the Holland Tunnel", FAR, "E")],
    "Midtown East, 2nd Ave & 45th St": [("FDR Drive", W500, "E"), ("2nd Avenue", W200, "NW"), ("1st Avenue", W200, "SE"),
                                        ("the Long Island Expressway", FAR, "SW")],
    "Fort Lee, NJ": [("I-95 (GW Bridge)", W500, "N"), ("Schlosser Street", W200, "E"), ("Lemoine Avenue", W200, "W"),
                     ("the Palisades Interstate Parkway", FAR, "NE")],
    "Long Island City, Queens Plaza": [("Queens Boulevard", W200, "NE"), ("the Queensboro Bridge", W200, "NW")],
    "Kew Gardens": [("the Van Wyck Expressway", W500, "NE"), ("the Jackie Robinson Parkway", W500, "NW"),
                    ("Queens Boulevard", W200, "NE"), ("the Grand Central Parkway", FAR, "N")],
    "Hunts Point, Bronx": [("the Bruckner Expressway", W500, "NW")],
}

@pytest.fixture(scope="session")
def nyc():
    return RoadFile.load(FIXTURES / "nyc-2026-11-21-clip.json.gz")


def bearings_in(a):
    """Every whole degree of the arc."""
    lo, hi = a
    if a == (0, 360):
        return range(360)
    return range(lo, hi + 1) if lo <= hi else [*range(lo, 360), *range(0, hi + 1)]


@pytest.mark.parametrize("label,lat,lon", POINTS)
def test_followed_roads_at_the_16_points(nyc, label, lat, lon):
    got = [(f.name, f.band, f.side) for f in find_roads(lat, lon, nyc) if f.followed]
    assert got == FIXED[label], label


def test_there_are_16_points():
    assert len(POINTS) == 16


def test_yorkville_is_on_several_sides_and_sides_are_names(nyc):
    fdr = next(f for f in find_roads(40.7750, -73.9450, nyc) if f.followed)
    assert fdr.toward == (0, 360) and sectors(fdr.toward) == list(SECTORS)
    assert all(f.side in SECTORS for lat, lon in COORDS for f in find_roads(lat, lon, nyc))


def test_sectors_hold_every_bearing_of_the_arc(nyc):
    for lat, lon in COORDS:
        for f in find_roads(lat, lon, nyc):
            for a in filter(None, (f.toward, f.toward_calm)):
                assert all(reaches(tuple(sectors(a)), b) for b in bearings_in(a))
    assert sectors((289, 95)) == ["N", "NE", "E", "W", "NW"] and reaches((289, 95), 10) and not reaches((289, 95), 180)


def test_list_is_capped_and_followed_come_first(nyc):
    for lat, lon in COORDS:
        found = find_roads(lat, lon, nyc)
        assert len(found) <= 6 and sum(f.followed for f in found) <= 4
        flags = [f.followed for f in found]
        assert flags == sorted(flags, reverse=True)
        assert all(not f.followed for f in found if f.tier == 4)


def test_arcs_and_sides():
    assert arc([]) is None
    assert arc([10, 20, 30]) == (10, 30)
    assert arc([350, 10]) == (350, 10)   # across north
    assert arc([0, 100, 200]) == (0, 360)   # wider than 180
    assert arc([90]) == (90, 90)
    assert [side_name(b) for b in (0, 22, 23, 90, 180, 270, 359)] == ["N", "N", "NE", "E", "S", "W", "N"]
    assert sectors((350, 10)) == ["N"] and reaches(("N", "NE"), 20) and not reaches(("N", "NE"), 90)


def test_entry_writes_the_config_values(nyc):
    found = find_roads(40.7127, -73.9590, nyc)[0]
    e = entry(found, "nyc-2026-11.json.gz", "US")
    assert e["name"] == "the BQE" and e["kind"] == "highway" and e["found"] == "auto" and e["on"] is True
    assert e["data"] == "nyc-2026-11.json.gz" and e["country"] == "US" and e["band"] == W500 and e["side"] == "E"
    assert isinstance(e["toward"], list) and isinstance(e["toward_calm"], list)
    far = next(f for f in find_roads(40.6150, -74.1320, nyc) if f.followed)
    e = entry(far, "x", "US")
    assert "toward" not in e and e["toward_calm"] and e["on"] is True
    off = [f for f in find_roads(40.7310, -73.9760, nyc) if not f.followed]
    assert off and entry(off[0], "x", "US")["on"] is False


def test_an_arc_read_back_from_config_is_a_list():
    assert sectors([0, 360]) == list(SECTORS) and reaches([0, 360], 123)
    assert sectors([289, 95]) == ["N", "NE", "E", "W", "NW"] and reaches([289, 95], 10) and not reaches([289, 95], 180)
