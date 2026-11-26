"""Road timing (road_timing.py): busy hours, the flag, strength and the until; and the slow hours the build
takes from NYC DOT's speeds (scripts/roads/speeds.py). No network."""
import importlib.util
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import roads
from config import ConfigError, RoadEntry, load_config, set_roads
from road_timing import all_until, busy_windows, road_now, road_until

ROOT = Path(__file__).resolve().parent.parent
SPEEDS = ROOT / "tests" / "fixtures" / "roads" / "bqe-speeds.json"
spec = importlib.util.spec_from_file_location("roads_speeds", ROOT / "scripts" / "roads" / "speeds.py")
speeds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speeds)
slow_windows = speeds.slow_windows

NY = ZoneInfo("America/New_York")
SUNRISE = datetime(2026, 12, 1, 7, 0, tzinfo=NY)


def _road(name, kind, band, side, toward, toward_calm):
    return RoadEntry(name=name, kind=kind, key=name.lower().replace(" ", "-"), data="nyc-2026-11.json.gz", country="US",
                     band=band, side=side, toward=toward, toward_calm=toward_calm, aadt=None, trucks_pct=None,
                     found="auto", on=True)


# The arcs from the spec's "Measured": the BQE at Williamsburg 74-162 (2 km: 55-202). Queens Boulevard at Rego Park spans
# 178° on its north-east side (the spec gives the width only: 316-134 is that width, across north). The SIE is far from
# Westerleigh (no arc within 500 m); its 2 km arc holds 180 (the spec gives no number).
BQE = _road("the BQE", "highway", "within 500 m", "E", (74, 162), (55, 202))
QUEENS_BLVD_US = _road("Queens Boulevard", "main road", "within 200 m", "NE", (316, 134), (315, 135))
SIE_FAR = _road("the Staten Island Expressway", "highway", "500 m to 1 km", "S", None, (150, 215))


def test_bqe_slow_hours_from_its_speeds():
    assert slow_windows(json.loads(SPEEDS.read_text())["medians"]) == [[7, 21]]


def test_flag_and_strength_for_williamsburg():   # Done when 8 and 15
    bqe = replace(BQE, slow=((7, 21),))
    tue = lambda hh, mm=0: datetime(2026, 12, 1, hh, mm, tzinfo=NY)
    on = lambda t: road_now(bqe, t, 120, 9, SUNRISE)["reaching"]
    assert on(tue(13)) and on(tue(20, 30)) and not on(tue(3)) and not on(tue(21, 30))
    assert [road_now(bqe, t, 120, 9, SUNRISE)["strength"] for t in (tue(13), tue(6, 30), tue(3))] == ["peak", "high", "low"]
    assert road_now(QUEENS_BLVD_US, tue(13), 0, 9, SUNRISE)["strength"] == "high"
    leaves = [{"time": tue(h), "wind_dir": 120 if h < 16 else 300, "wind_kmh": 9} for h in range(14, 24)]
    assert all_until([bqe], tue(13), leaves, {tue(0).date(): SUNRISE}).hour == 16


def test_calm_morning_far_road_westerleigh():   # Done when 8
    sie = SIE_FAR   # no toward; toward_calm holds 180
    for minutes in (-120, -90, -1):
        assert road_now(sie, SUNRISE + timedelta(minutes=minutes), 180, 4, SUNRISE) == {"reaching": True, "strength": "peak", "why": "calm"}
    assert not road_now(sie, SUNRISE - timedelta(minutes=30), 180, 7, SUNRISE)["reaching"]
    for minutes in (-121, 0, 30, 120):
        assert not road_now(sie, SUNRISE + timedelta(minutes=minutes), 180, 4, SUNRISE)["reaching"]


@pytest.mark.parametrize("sunrise_minute", [-13, 17])
def test_calm_until_ends_at_sunrise_between_forecast_hours(sunrise_minute):
    sunrise = SUNRISE + timedelta(minutes=sunrise_minute)
    at = SUNRISE - timedelta(minutes=30)
    current = {"time": at, "wind_dir": 180, "wind_kmh": 4}
    hourly = [{"time": SUNRISE + timedelta(hours=h), "wind_dir": 180, "wind_kmh": 4} for h in (0, 1, 2)]
    suns = {sunrise.date(): sunrise}
    assert road_until(SIE_FAR, at, [current, *hourly], suns) == sunrise
    assert all_until([SIE_FAR], at, [current, *hourly], suns) == sunrise
    # A wind change ends the action earlier; missing weather still prevents an end time.
    assert road_until(SIE_FAR, at, [current, {**hourly[0], "wind_dir": 0}, *hourly[1:]], suns) == min(sunrise, SUNRISE)
    assert road_until(SIE_FAR, at, [current, {**hourly[0], "wind_kmh": None}, *hourly[1:]], suns) == (sunrise if sunrise < SUNRISE else None)
    # Another road can keep reaching in busy hours after calm reach ends.
    windier = [{**r, "wind_dir": 120, "wind_kmh": 9} for r in hourly]
    assert all_until([SIE_FAR, BQE], at, [{"time": at, "wind_dir": 120, "wind_kmh": 9}, *windier], suns) is None


def test_no_weather_and_weekends():   # Review Focus 2, 3, 5
    bqe = replace(BQE, slow=((7, 21),), toward=(289, 95))
    sat = datetime(2026, 12, 5, 6, 30, tzinfo=NY)
    assert road_now(bqe, sat, 10, 9, SUNRISE)["why"] == "busy"            # weekend: class defaults 6–22, not slow hours
    assert road_now(bqe, sat, None, None, None) == {"reaching": False, "strength": "high", "why": "busy"}
    assert all_until([bqe], sat, [], {}) is None
    for t in (datetime(2026, 12, 1, h, tzinfo=NY) for h in range(24)):     # reaching is never low
        r = road_now(bqe, t, 10, 9, SUNRISE)
        assert not (r["reaching"] and r["strength"] == "low")


def test_missing_weather_no_road_reaches_and_no_until():   # Review Focus 2, each way the weather can be missing
    bqe = replace(BQE, slow=((7, 21),))
    tue = lambda hh: datetime(2026, 12, 1, hh, tzinfo=NY)
    assert road_now(bqe, tue(13), 120, 9, SUNRISE)["reaching"]
    hours_only = {"reaching": False, "strength": "peak", "why": "slow"}
    assert road_now(bqe, tue(13), None, 9, SUNRISE) == hours_only          # no wind direction
    assert road_now(bqe, tue(13), 120, None, SUNRISE) == hours_only        # no wind speed
    assert road_now(bqe, tue(13), 120, 9, None) == {**hours_only, "reaching": True}  # busy reach needs only wind
    assert road_now(SIE_FAR, SUNRISE - timedelta(minutes=30), 180, 4, None)["reaching"] is False
    leaves = [{"time": tue(h), "wind_dir": 120 if h < 16 else 300, "wind_kmh": 9} for h in range(14, 24)]
    assert all_until([bqe], tue(13), [], {SUNRISE.date(): SUNRISE}) is None     # no hourly rows
    assert all_until([bqe], tue(13), leaves, {}) == tue(16)                      # no sunrise needed
    assert road_until(bqe, tue(13), leaves, {}) == tue(16)


def test_arcs_from_config_lists_across_north_and_every_side():   # Review Focus 3, arcs as config.toml gives them
    tue = datetime(2026, 12, 1, 13, tzinfo=NY)
    north = replace(BQE, slow=((7, 21),), toward=[289, 95])
    assert road_now(north, tue, 10, 9, SUNRISE)["reaching"] and road_now(north, tue, 300, 9, SUNRISE)["reaching"]
    assert not road_now(north, tue, 200, 9, SUNRISE)["reaching"]
    every = replace(BQE, slow=((7, 21),), toward=[0, 360])
    assert all(road_now(every, tue, d, 9, SUNRISE)["reaching"] for d in (0, 90, 200, 359))
    calm = replace(SIE_FAR, toward_calm=[350, 20])
    morning = SUNRISE - timedelta(minutes=30)
    assert road_now(calm, morning, 5, 4, SUNRISE)["reaching"] and not road_now(calm, morning, 180, 4, SUNRISE)["reaching"]


def test_busy_windows_order_and_the_hand_set_rush():
    rush = replace(BQE, rush=((7, 10, "morning"), (16, 20, "evening")), slow=((7, 21),))
    assert busy_windows(rush, False) == (((7, 10), (16, 20)), "rush")      # a hand-set rush applies every day
    assert busy_windows(replace(BQE, slow=((7, 21),)), True) == (((7, 21),), "slow")
    assert busy_windows(QUEENS_BLVD_US, True) == (((7, 20),), "busy")
    assert busy_windows(replace(BQE, country="DE"), True) == (((7, 10), (16, 20)), "busy")


def test_until_stops_at_unknown_wind_and_at_twelve_hours():
    bqe = replace(BQE, slow=((7, 21),))
    tue = lambda hh: datetime(2026, 12, 1, hh, tzinfo=NY)
    suns = {SUNRISE.date(): SUNRISE, (SUNRISE + timedelta(days=1)).date(): SUNRISE + timedelta(days=1)}
    blind = [{"time": tue(14), "wind_dir": None, "wind_kmh": None}, {"time": tue(15), "wind_dir": 300, "wind_kmh": 9}]
    assert road_until(bqe, tue(13), blind, suns) is None                   # an hour without wind: no time is given
    rush = replace(BQE, rush=((0, 24, "all day"),))
    stays = [{"time": tue(13) + timedelta(hours=k), "wind_dir": 120, "wind_kmh": 9} for k in range(1, 14)]
    assert road_until(rush, tue(13), stays, suns) is None                 # still on 12 hours on
    utc = [{"time": (tue(13) + timedelta(hours=k)).astimezone(ZoneInfo("UTC")), "wind_dir": 120, "wind_kmh": 9} for k in range(1, 12)]
    assert road_until(bqe, tue(13), utc, suns) == tue(21)                  # rows in UTC are read in the home's time


def test_slow_is_read_from_config_and_written_by_entry():
    found = roads.Found(key="bqe", name="the BQE", short="BQE", kind="highway", cls="motorway", tier=1, band="within 500 m",
                        side="E", toward=(74, 162), toward_calm=(55, 202), aadt=None, trucks_pct=None, followed=True,
                        slow=((7, 21),))
    e = roads.entry(found, "nyc-2026-11.json.gz", "US")
    assert e["slow"] == [[7, 21]]
    reference = (ROOT / "tests" / "fixtures" / "config_reference.toml").read_text(encoding="utf-8")
    cfg = load_config({"CONFIG_TOML": set_roads(reference, [e])}, ROOT)
    assert cfg.roads[-1].slow == ((7, 21),) and cfg.roads[0].slow == ()
    for bad in ([[21, 7]], [[7, 25]], [[7]], [7, 21]):
        with pytest.raises(ConfigError, match="the BQE.*slow"):
            load_config({"CONFIG_TOML": set_roads(reference, [{**e, "slow": bad}])}, ROOT)


def test_speed_rows_weekdays_only_and_the_query():
    rows = [{"id": "148", "speed": "40", "data_as_of": "2026-11-30T13:05:00.000"},    # Monday
            {"id": "148", "speed": "20", "data_as_of": "2026-12-01T13:40:00.000"},    # Tuesday
            {"id": "148", "speed": "30", "data_as_of": "2026-12-02T13:10:00.000"},    # Wednesday
            {"id": "148", "speed": "5", "data_as_of": "2026-12-05T13:10:00.000"},     # Saturday: left out
            {"id": "148", "speed": "x", "data_as_of": "2026-12-01T14:00:00.000"}]     # unreadable: left out
    assert speeds.hourly_medians(rows) == {"148": {13: 30.0}}
    asked = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return rows[:2]

    def get(url, params, timeout):
        asked.append((url, params))
        return Resp()
    assert speeds.fetch(["148", "157"], date(2026, 11, 1), get=get) == rows[:2]
    url, params = asked[0]
    assert url == speeds.SPEEDS_URL and params["$limit"] == 50000 and len(asked) == 1
    assert "id in ('148','157')" in params["$where"] and "data_as_of >= '2026-11-01T00:00:00'" in params["$where"]


def test_zero_speeds_are_kept_as_stop_and_go():
    rows = [{"id": "148", "speed": v, "data_as_of": f"2026-12-01T08:{m:02d}:00.000"} for m, v in ((5, "0"), (10, "0"), (15, "10"))]
    assert speeds.hourly_medians(rows) == {"148": {8: 0.0}}


def test_an_hour_of_only_zero_speeds_is_an_outage():
    rows = [{"id": "148", "speed": "0", "data_as_of": f"2026-12-01T09:{m:02d}:00.000"} for m in (5, 10, 15)]
    rows += [{"id": "148", "speed": "30", "data_as_of": "2026-12-01T10:05:00.000"},
             {"id": "157", "speed": "0", "data_as_of": "2026-12-01T09:05:00.000"}]
    assert speeds.hourly_medians(rows) == {"148": {10: 30.0}}          # 148's 9 AM dropped; 157 has no hour left


def test_links_join_the_road_they_run_along():
    line = lambda lat, w=-73.95, e=-73.94: [[w, lat], [e, lat]]
    feats = [{"properties": {"g": "bqe", "c": "motorway"}, "geometry": {"coordinates": line(40.7127)}},
             {"properties": {"g": "street", "c": "primary"}, "geometry": {"coordinates": line(40.7140, -73.95, -73.92)}},   # 145 m north
             {"properties": {"g": None, "c": "motorway", "l": 1}, "geometry": {"coordinates": line(40.71272)}}]   # a ramp
    links = speeds.merge_links([
        {"id": "148", "link_name": "on the BQE", "link_points": "40.71275,-73.949 40.71276,-73.945 40.7128,-73.941"},
        {"id": "999", "link_name": "elsewhere", "link_points": "40.7200,-73.949 40.7201,-73.941"},
        {"id": "500", "link_name": "on the street", "link_points": "40.71390,-73.948 bad 40.71395,-73.925"},
        {"id": "501", "link_name": "across both", "link_points": "40.7100,-73.945 40.7170,-73.945"}])
    assert speeds.join_links(links, {"bqe": {}, "street": {}}, feats) == {"148": "bqe", "500": "street"}
    under = feats + [{"properties": {"g": "a-street-under", "c": "primary"}, "geometry": {"coordinates": line(40.71271)}}]
    classes = {"bqe": {"class": "motorway"}, "street": {"class": "primary"}, "a-street-under": {"class": "primary"}}
    assert speeds.join_links(links[:1], classes, under) == {"148": "bqe"}   # a tie goes to the higher class


def test_a_link_line_from_the_encoded_polyline():
    rows = [{"id": "7", "link_name": "x", "link_points": "40.7,-74.0", "encoded_poly_line": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
            {"id": "7", "link_name": "x", "link_points": "40.7,-74.0", "encoded_poly_line": "_p~iF~ps|U_ulLnnqC_mqNvxq`@??"}]
    assert speeds.merge_links(rows)[0]["line"] == [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]   # Google's example
    assert speeds.merge_links(rows, box=[-121, 38, -120, 41])[0]["line"] == [[-120.2, 38.5], [-120.95, 40.7]]


def test_slow_hours_of_the_stretch_near_the_home():   # the coordinator's ruling: per stretch, not per whole road
    doc = json.loads(SPEEDS.read_text())
    own = {k: slow_windows({k: m}) for k, m in doc["medians"].items()}
    assert own == {"148": [[15, 19]], "157": [[7, 21]]}
    data = roads.RoadFile.load(ROOT / "tests" / "fixtures" / "roads" / "nyc-2026-11-21-clip.json.gz")
    bqe = lambda: next(f for f in roads.find_roads(40.7127, -73.9590, data) if f.key == "bqe")   # Williamsburg
    far = {"id": "259", "slow": [[5, 22]], "line": doc["lines"]["259"]}
    data.roads["bqe"] = {**data.roads["bqe"], "slow": [[5, 22]],
                         "links": [{"id": k, "slow": own[k], "line": doc["lines"][k]} for k in ("148", "157")] + [far]}
    assert bqe().slow == ((7, 21),)                  # 148 and 157 pass within 1 km; 259, 5 km away, doesn't count
    data.roads["bqe"]["links"] = [far]
    assert bqe().slow == ((5, 22),)                  # no link that close: the whole road's
    del data.roads["bqe"]["links"], data.roads["bqe"]["slow"]
    assert bqe().slow == ()                          # none: the class defaults apply
    assert roads.union_windows([[[7, 9]], [[8, 12], [15, 16]]]) == ((7, 12), (15, 16))


def test_build_writes_slow_hours_and_copies_them_when_the_feed_is_stale(tmp_path):
    bspec = importlib.util.spec_from_file_location("roads_build_speeds", ROOT / "scripts" / "roads" / "build.py")
    build = importlib.util.module_from_spec(bspec)
    bspec.loader.exec_module(build)
    feats = [{"properties": {"g": "bqe", "c": "motorway"}, "geometry": {"coordinates": [[-73.95, 40.7127], [-73.94, 40.7127]]}}]
    links = [{"id": "157", "link_name": "BQE S", "link_points": "40.71275,-73.949 40.7128,-73.941"}]
    medians = json.loads(SPEEDS.read_text())["medians"]["157"]
    fresh = [{"id": "157", "speed": str(v), "data_as_of": f"2026-12-01T{int(h):02d}:10:00.000"} for h, v in medians.items()]

    def served(rows):
        class Resp:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                pass

            def json(self):
                return self.body
        return lambda url, params, timeout: Resp(links if "$group" in params else rows)
    meta = build.metro("nyc")
    roads_ = {"bqe": {"name": "the BQE"}}
    lines = build.add_speeds(meta, roads_, feats, date(2026, 12, 3), get=served(fresh))
    assert roads_["bqe"]["slow"] == [[7, 21]] and any("joined to the BQE" in l for l in lines)
    assert [(x["id"], x["slow"]) for x in roads_["bqe"]["links"]] == [("157", [[7, 21]])] and len(roads_["bqe"]["links"][0]["line"]) == 2
    build.write_metro(meta, roads_, feats, tmp_path, date(2026, 12, 3), "2026-11-21")
    stale = {"bqe": {"name": "the BQE"}}
    lines = build.add_speeds(meta, stale, feats, date(2026, 12, 20), previous=tmp_path, get=served(fresh))
    assert stale["bqe"]["slow"] == [[7, 21]] and stale["bqe"]["links"] == roads_["bqe"]["links"]
    assert "copied from nyc-2026-12.json.gz" in lines[-1]
    none = {"bqe": {"name": "the BQE"}}
    build.add_speeds(meta, none, feats, date(2026, 12, 20), get=served(fresh))
    assert "slow" not in none["bqe"]


@pytest.mark.parametrize('day,hour,minute,fold', [(8, 0, 30, 0), (1, 1, 0, 1)])
def test_calm_window_is_two_elapsed_hours_across_clock_changes(day, hour, minute, fold):
    month = 3 if day == 8 else 11
    sunrise = datetime(2026, month, day, 3, 30 if month == 3 else 0, tzinfo=NY)
    at = datetime(2026, month, day, hour, minute, tzinfo=NY, fold=fold)
    assert road_now(SIE_FAR, at, 180, 4, sunrise)['reaching']
    before = (sunrise.astimezone(ZoneInfo('UTC')) - timedelta(hours=2, seconds=1)).astimezone(NY)
    assert not road_now(SIE_FAR, before, 180, 4, sunrise)['reaching']


def test_sunrise_does_not_end_busy_action_with_stale_wind():
    at = SUNRISE.replace(hour=6, minute=30)
    sunrise = SUNRISE.replace(minute=47)
    hourly = [{'time': at.replace(hour=5, minute=0), 'wind_dir': 300, 'wind_kmh': 9},
              {'time': SUNRISE.replace(hour=8), 'wind_dir': 120, 'wind_kmh': 9},
              {'time': SUNRISE.replace(hour=9), 'wind_dir': 300, 'wind_kmh': 9}]
    assert road_until(BQE, at, hourly, {sunrise.date(): sunrise}) == SUNRISE.replace(hour=9)


def test_busy_until_ends_between_hourly_rows():
    at = SUNRISE.replace(hour=20, minute=30)
    road = replace(BQE, slow=((7, 21),))
    hourly = [{'time': at.replace(hour=20, minute=0), 'wind_dir': 120, 'wind_kmh': 9},
              {'time': at.replace(hour=22, minute=0), 'wind_dir': 120, 'wind_kmh': 9}]
    assert road_until(road, at, hourly, {SUNRISE.date(): SUNRISE}) == at.replace(hour=21, minute=0)


def test_far_toward_and_forecast_covers():
    """A road beyond the day's reach: the wind toward the home is read on its calm arc. A reaching road with no end
    reaches for at least UNTIL_HOURS only when the wind is known that far."""
    from types import SimpleNamespace
    import road_timing
    far = SimpleNamespace(toward=None, toward_calm=(90, 180))
    assert road_timing.far_toward(far, 135) and not road_timing.far_toward(far, 300)
    assert not road_timing.far_toward(SimpleNamespace(toward=(90, 180), toward_calm=(90, 180)), 135)
    at = datetime(2026, 4, 21, 8, tzinfo=ZoneInfo("America/New_York"))
    hours = [{"time": at + timedelta(hours=h), "wind_dir": 135, "wind_kmh": 9} for h in range(0, 14)]
    assert road_timing.forecast_covers(at, hours)
    assert not road_timing.forecast_covers(at, hours[:6])   # the forecast stops after 5 hours
    assert not road_timing.forecast_covers(at, [*hours[:4], {**hours[4], "wind_dir": None}, *hours[5:]])
