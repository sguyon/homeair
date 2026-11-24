import json
from pathlib import Path

import pytest

from config import Config, ConfigError, bearing_in, load_config, public_view, toward_range

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = (REPO / "config.example.toml").read_text(encoding="utf-8")


def root_with(tmp_path, text=EXAMPLE, name="config.toml"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")
    return tmp_path


def test_example_loads(tmp_path):
    cfg = load_config({}, root_with(tmp_path))
    assert isinstance(cfg, Config)
    assert cfg.profiles == {"toddler": True, "sinus": True, "runner": True}
    assert cfg.road is None
    assert cfg.demo is False


def test_old_needs_table_still_reads(tmp_path):
    """[needs] is [profiles]' old name (v0.1): a config that still says it keeps working."""
    old = EXAMPLE.replace("[profiles]", "[needs]").replace("runner = true", "runner = false")
    assert "[profiles]" not in old
    assert load_config({}, root_with(tmp_path, old)).profiles == {"toddler": True, "sinus": True, "runner": False}


def test_profiles_win_over_the_old_needs_table(tmp_path):
    both = EXAMPLE + "\n[needs]\ntoddler = false\nsinus = false\nrunner = false\n"
    assert load_config({}, root_with(tmp_path, both)).profiles == {"toddler": True, "sinus": True, "runner": True}


def test_env_wins_over_file(tmp_path):
    root = root_with(tmp_path, EXAMPLE.replace('"Maplewood"', '"File"'))
    cfg = load_config({"CONFIG_TOML": EXAMPLE.replace('"Maplewood"', '"Env"')}, root)
    assert cfg.place.name == "Env"
    assert cfg.source == "CONFIG_TOML"


def test_demo_flag_reads_demo_file(tmp_path):
    root_with(tmp_path, EXAMPLE, "demo/config.toml")
    cfg = load_config({"DEMO": "1"}, tmp_path)
    assert cfg.demo is True
    assert cfg.source == "demo"


def test_nothing_returns_none(tmp_path):
    assert load_config({}, tmp_path) is None


def test_crlf_bom_and_quotes_parse(tmp_path):
    crlf = "﻿" + EXAMPLE.replace("\n", "\r\n")
    assert load_config({"CONFIG_TOML": crlf}, tmp_path).place.name == "Maplewood"
    assert load_config({"CONFIG_TOML": "'" + crlf + "'"}, tmp_path).place.name == "Maplewood"


def test_bad_toml_names_source_and_line(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_config({"CONFIG_TOML": "[place\n"}, tmp_path)
    assert "CONFIG_TOML" in str(e.value) and "line 1" in str(e.value)


def test_missing_required_key(tmp_path):
    text = EXAMPLE.replace("lat = 39.95\n", "")
    with pytest.raises(ConfigError) as e:
        load_config({"CONFIG_TOML": text}, tmp_path)
    assert "place.lat" in str(e.value)


def test_toward_range_matches_spec():
    route = json.loads((REPO / "static" / "bqe.json").read_text())
    lo, hi = toward_range(40.73, -73.95, route)
    # Research said (83, 194); the specified algorithm computes (89, 192). Pinned to the computed value.
    assert (lo, hi) == (89, 192)
    lo, hi = toward_range(40.7192, -73.9500, route)
    assert abs(lo - 63) <= 3 and abs(hi - 210) <= 3


def test_override_wins(tmp_path):
    text = EXAMPLE.replace("# [road]", "[road]").replace("# name = \"BQE\"", "name = \"BQE\"") \
        .replace("# route =", "route =").replace("# rush =", "rush =").replace("# toward =", "toward =")
    cfg = load_config({"CONFIG_TOML": text}, REPO)
    assert cfg.road.toward == (80, 200)
    assert cfg.road.rush[0] == (7, 10, "morning")


def test_road_without_toward_is_computed(tmp_path):
    text = EXAMPLE.replace("lat = 39.95", "lat = 40.73").replace("lon = -75.16", "lon = -73.95")
    text += '\n[road]\nname = "BQE"\nroute = "static/bqe.json"\nrush = [[7, 10, "morning"]]\n'
    cfg = load_config({"CONFIG_TOML": text}, REPO)
    assert cfg.road.toward == toward_range(40.73, -73.95, json.loads((REPO / "static/bqe.json").read_text()))


def test_public_view_has_no_secrets(tmp_path):
    cfg = load_config({"AIRGRADIENT_TOKEN": "x"}, root_with(tmp_path))
    assert cfg is not None
    assert '"x"' not in json.dumps(public_view(cfg))
    cfg = load_config({"AIRGRADIENT_TOKEN": "sekret-token", "CONFIG_TOML": EXAMPLE}, tmp_path)
    assert "sekret-token" not in json.dumps(public_view(cfg))
    assert public_view(cfg)["place"]["lat"] == 39.95


def test_toward_range_wraps_north():
    # points due north of home, spread slightly east and west
    route = [[-75.16 + dx, 39.95 + 0.01] for dx in (-0.002, 0.0, 0.002)]
    lo, hi = toward_range(39.95, -75.16, route)
    assert lo > hi and (360 - lo) + hi < 30
    assert bearing_in(0, (lo, hi)) and not bearing_in(180, (lo, hi))


def test_bearing_in_plain_arc():
    assert bearing_in(100, (80, 200)) and not bearing_in(10, (80, 200)) and not bearing_in(250, (80, 200))


def _bad(tmp_path, text):
    with pytest.raises(ConfigError) as e:
        load_config({"CONFIG_TOML": text}, REPO)
    return str(e.value)


def test_limits_not_a_table():
    msg = _bad(None, "limits = 5\n" + EXAMPLE.replace("[limits]", "[limits_gone]"))
    assert "CONFIG_TOML" in msg and "limits" in msg


def test_road_not_a_table():
    msg = _bad(None, "road = 3\n" + EXAMPLE)
    assert "CONFIG_TOML" in msg and "road" in msg


def test_limit_not_a_number():
    msg = _bad(None, EXAMPLE.replace("co2 = 800", 'co2 = "high"'))
    assert "CONFIG_TOML: limits.co2 must be a number" in msg


def test_bad_rush_entry():
    text = EXAMPLE + '\n[road]\nname = "R"\nroute = "static/bqe.json"\nrush = [[7, 10]]\ntoward = [80, 200]\n'
    assert "road.rush" in _bad(None, text)


def test_bad_toward():
    text = EXAMPLE + '\n[road]\nname = "R"\nroute = "static/bqe.json"\ntoward = [80]\n'
    assert "road.toward" in _bad(None, text)


def test_no_route_point_near_home_names_source():
    text = EXAMPLE + '\n[road]\nname = "R"\nroute = "static/bqe.json"\n'
    assert _bad(None, text).startswith("CONFIG_TOML: road:")


def test_unreadable_config_file(tmp_path):
    (tmp_path / "config.toml").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ConfigError) as e:
        load_config({}, tmp_path)
    assert "config.toml" in str(e.value)


def test_unknown_timezone_is_a_config_error(tmp_path):
    root = root_with(tmp_path, EXAMPLE.replace('timezone = "America/New_York"', 'timezone = "Nowhere/Town"'))
    with pytest.raises(ConfigError, match="place.timezone 'Nowhere/Town'"):
        load_config({}, root)


def test_road_kind_defaults_to_highway():
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    text = (ROOT / "tests/fixtures/config_reference.toml").read_text()
    cfg = load_config({"CONFIG_TOML": text}, ROOT)
    assert cfg.road.kind == "expressway" and public_view(cfg)["road"]["kind"] == "expressway"
    plain = load_config({"CONFIG_TOML": text.replace('kind = "expressway"\n', "", 1)}, ROOT)
    assert plain.road.kind == "highway"



def test_config_file_points_at_another_path(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    f = tmp_path / "mounted.toml"
    f.write_text((root / "tests/fixtures/config_reference.toml").read_text().replace('name = "Greenpoint"', 'name = "Mounted"', 1))
    assert load_config({"CONFIG_FILE": str(f)}, root).place.name == "Mounted"
    env_text = (root / "tests/fixtures/config_reference.toml").read_text().replace('name = "Greenpoint"', 'name = "Env"', 1)
    assert load_config({"CONFIG_FILE": str(f), "CONFIG_TOML": env_text}, root).place.name == "Env"


def test_config_file_missing_is_an_error_not_a_fallback(tmp_path):
    (tmp_path / "config.toml").write_text('[place]\nname = "Root"\nlat = 1.0\nlon = 1.0\ntimezone = "UTC"\n')
    with pytest.raises(ConfigError, match="CONFIG_FILE"):
        load_config({"CONFIG_FILE": str(tmp_path / "nope.toml")}, tmp_path)


def test_rh_good_defaults_to_50_and_reaches_the_page():
    plain = load_config({"CONFIG_TOML": EXAMPLE.replace("rh_good = 50\n", "")})
    assert plain.limits["rh_good"] == 50 and public_view(plain)["limits"]["rh_good"] == 50
    set_ = load_config({"CONFIG_TOML": EXAMPLE.replace("rh_good = 50", "rh_good = 45")})
    assert set_.limits["rh_good"] == 45


def test_appliances_default_to_none_without_a_home_section():
    text = EXAMPLE.split("[home]")[0] + "[limits]" + EXAMPLE.split("[limits]", 1)[1]
    assert "\n[home]\n" not in text
    assert load_config({"CONFIG_TOML": text}, REPO).appliances == ()


def test_appliances_from_the_home_section():
    one = EXAMPLE.replace('appliances = []', 'appliances = ["ac_dry"]')
    assert load_config({"CONFIG_TOML": one}).appliances == ("ac_dry",)
    none = EXAMPLE.replace('appliances = []', "appliances = []")
    assert load_config({"CONFIG_TOML": none}).appliances == ()


def test_unknown_appliance_is_named():
    bad = EXAMPLE.replace('appliances = []', 'appliances = ["dehumidifier", "humidifier", "purifier"]')
    with pytest.raises(ConfigError) as e:
        load_config({"CONFIG_TOML": bad})
    msg = str(e.value)
    assert "humidifier" in msg and "purifier" in msg and "dehumidifier, ac_dry" in msg
    with pytest.raises(ConfigError):
        load_config({"CONFIG_TOML": EXAMPLE.replace('appliances = []', 'appliances = "ac_dry"')})


@pytest.mark.parametrize("change, keys", [
    ("rh_good = 50", "rh_good = 25"),            # under rh_dry
    ("rh_good = 50", "rh_good = 65"),            # over rh_damp
    ("rh_stop = 55", "rh_stop = 65"),            # over rh_damp
    ("pm_high = 35", "pm_high = 10"),            # under pm
])
def test_limits_out_of_order_are_a_config_error(change, keys):
    with pytest.raises(ConfigError) as e:
        load_config({"CONFIG_TOML": EXAMPLE.replace(change, keys)})
    key = keys.split(" =")[0]
    assert "CONFIG_TOML" in str(e.value) and key in str(e.value)


def test_limits_in_order_load():
    assert load_config({"CONFIG_TOML": EXAMPLE.replace("rh_good = 50", "rh_good = 60")}).limits["rh_good"] == 60


def test_place_is_rounded_to_about_a_kilometer(tmp_path):
    """The home's position leaves the server (weather, pollen): it's rounded whatever the file says."""
    import config as cfgmod
    text = (cfgmod.ROOT / "config.example.toml").read_text()
    import re as _re
    text = _re.sub(r"(?m)^lat\s*=.*$", "lat = 40.712345", text)
    text = _re.sub(r"(?m)^lon\s*=.*$", "lon = -73.956789", text)
    c = cfgmod.load_config({"CONFIG_TOML": text})
    assert (c.place.lat, c.place.lon) == (40.71, -73.96)


def _place_only(tmp_path, drop, sources="", name="Maplewood"):
    """The example with some [place] lines dropped and an optional [sources] line added."""
    text = "\n".join(l for l in EXAMPLE.splitlines() if not any(l.startswith(d + " =") for d in drop))
    if sources:
        text = text.replace("[sources]\n", "[sources]\n" + sources + "\n", 1)
    return root_with(tmp_path, text)


@pytest.mark.parametrize("value, expected", [("900002", 900002), ('"area"', "area"), ('"model"', "model")])
def test_indoor_only_with_outdoor_from(tmp_path, value, expected):
    cfg = load_config({}, _place_only(tmp_path, ["outdoor"], f"outdoor_from = {value}"))
    assert cfg.place.outdoor is None and cfg.place.indoor == "living room"
    assert cfg.outdoor_from == expected


def test_outdoor_only_needs_no_outdoor_from(tmp_path):
    cfg = load_config({}, _place_only(tmp_path, ["indoor"]))
    assert cfg.place.indoor is None and cfg.place.outdoor == "porch" and cfg.outdoor_from is None


def test_neither_monitor_is_a_home_where_none_was_found_yet(tmp_path):
    cfg = load_config({}, _place_only(tmp_path, ["indoor", "outdoor"]))
    assert cfg.place.indoor is None and cfg.place.outdoor is None and cfg.outdoor_from is None


def test_indoor_only_without_outdoor_from_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="sources.outdoor_from"):
        load_config({}, _place_only(tmp_path, ["outdoor"]))


@pytest.mark.parametrize("value", ['"nearby"', "0", "-3", "true", "1.5"])
def test_bad_outdoor_from_is_refused(tmp_path, value):
    with pytest.raises(ConfigError, match='sources.outdoor_from must be a public monitor\'s id, "area" or "model"'):
        load_config({}, _place_only(tmp_path, ["outdoor"], f"outdoor_from = {value}"))


def test_both_monitors_ignore_outdoor_from(tmp_path):
    cfg = load_config({}, _place_only(tmp_path, [], "outdoor_from = 900002"))
    assert cfg.outdoor_from is None
    assert "outdoor_from" not in public_view(cfg)


def test_public_view_without_an_outdoor_monitor(tmp_path):
    v = public_view(load_config({}, _place_only(tmp_path, ["outdoor"], "outdoor_from = 900002")))
    assert v["place"]["outdoor"] is None and v["place"]["indoor"] == "living room"
    assert v["outdoor_from"] == {"kind": "monitor", "id": 900002}
    v = public_view(load_config({}, _place_only(tmp_path, ["outdoor"], 'outdoor_from = "area"')))
    assert v["outdoor_from"] == {"kind": "area", "id": None}
    v = public_view(load_config({}, _place_only(tmp_path, ["outdoor"], 'outdoor_from = "model"')))
    assert v["outdoor_from"] == {"kind": "model", "id": None}


def test_public_view_without_an_indoor_monitor(tmp_path):
    v = public_view(load_config({}, _place_only(tmp_path, ["indoor"])))
    assert v["place"]["indoor"] is None and v["place"]["outdoor"] == "porch"
    assert "outdoor_from" not in v


@pytest.mark.parametrize("blank", ['""', '"   "'])
def test_a_blank_outdoor_name_is_no_outdoor_monitor(tmp_path, blank):
    """An agent that blanks the line instead of deleting it gets the same home as deleting it."""
    text = EXAMPLE.replace('outdoor = "porch"', f"outdoor = {blank}").replace("[sources]\n", '[sources]\noutdoor_from = "area"\n', 1)
    cfg = load_config({}, root_with(tmp_path, text))
    assert cfg.place.outdoor is None and cfg.outdoor_from == "area"
    assert public_view(cfg)["place"]["outdoor"] is None


def test_a_blank_outdoor_name_still_needs_outdoor_from(tmp_path):
    with pytest.raises(ConfigError, match="sources.outdoor_from"):
        load_config({}, root_with(tmp_path, EXAMPLE.replace('outdoor = "porch"', 'outdoor = ""')))


@pytest.mark.parametrize("blank", ['""', '" "'])
def test_a_blank_indoor_name_is_no_indoor_monitor(tmp_path, blank):
    cfg = load_config({}, root_with(tmp_path, EXAMPLE.replace('indoor = "living room"', f"indoor = {blank}")))
    assert cfg.place.indoor is None and cfg.place.outdoor == "porch"


def test_two_blank_names_are_no_monitors(tmp_path):
    text = EXAMPLE.replace('indoor = "living room"', 'indoor = ""').replace('outdoor = "porch"', 'outdoor = " "')
    cfg = load_config({}, root_with(tmp_path, text))
    assert cfg.place.indoor is None and cfg.place.outdoor is None


def test_temp_unit_defaults_to_celsius_and_reads_the_home_section():
    # decision 8 (Sacha, 2026-11-12): a [home] unit, set by setup from the country; the page starts in it
    no_home = EXAMPLE.split("[home]")[0] + "[limits]" + EXAMPLE.split("[limits]", 1)[1]
    assert load_config({"CONFIG_TOML": no_home}, REPO).temp_unit == "C"
    assert load_config({"CONFIG_TOML": EXAMPLE}).temp_unit == "C"   # the example says it, set to C
    f = EXAMPLE.replace('temp_unit = "C"', 'temp_unit = "F"')
    assert load_config({"CONFIG_TOML": f}).temp_unit == "F"
    with pytest.raises(ConfigError) as e:
        load_config({"CONFIG_TOML": EXAMPLE.replace('temp_unit = "C"', 'temp_unit = "K"')})
    assert "home.temp_unit" in str(e.value) and '"F" or "C"' in str(e.value)


def test_public_view_gives_the_page_its_starting_unit():
    f = load_config({"CONFIG_TOML": EXAMPLE.replace('temp_unit = "C"', 'temp_unit = "F"')})
    assert public_view(f)["temp_unit"] == "F"
    assert public_view(load_config({"CONFIG_TOML": EXAMPLE}))["temp_unit"] == "C"


def test_the_demo_home_in_brooklyn_starts_in_fahrenheit():
    # Sacha, 2026-11-13: "Build °F for US homes now, the demo in °F too"
    cfg = load_config({"DEMO": "1"}, REPO)
    assert cfg.demo and cfg.temp_unit == "F" and public_view(cfg)["temp_unit"] == "F"


SERIAL = "84FCE612345A"


@pytest.mark.parametrize("host, serial, ok", [
    ("8.8.8.8", True, False),                 # a public address: the server would fetch the internet
    ("monitor.local", True, False),           # a name: resolved by whoever answers
    ("http://192.168.1.5/x", True, False),    # a scheme and a path
    ("192.168.1.5", False, False),            # no serial: nothing to check the answer against
    ("192.168.1.5", True, True),
    ("169.254.3.4", True, True),
    ("127.0.0.1:8081", True, True),
])
def test_host_must_be_a_private_ipv4_with_a_serial(tmp_path, host, serial, ok):
    """check 20: what the server may be made to fetch is the home's own network, and only for a monitor it can verify."""
    lines = f'indoor_serial = "{SERIAL}"\n' if serial else ""
    text = EXAMPLE.replace('indoor = "living room"', f'indoor = "living room"\n{lines}indoor_host = "{host}"', 1)
    if not ok:
        with pytest.raises(ConfigError, match="indoor_host"):
            load_config({}, root_with(tmp_path, text))
        return
    p = load_config({}, root_with(tmp_path, text)).place
    assert p.indoor_host == host and p.indoor_serial == SERIAL.lower()


def test_set_monitor_keys_keeps_comments_and_order():
    """The app rewrites the household's hand-edited file: only the monitor keys change, every other line stays."""
    from config import set_monitor_keys
    ref = (Path(__file__).parent / "fixtures" / "config_reference.toml").read_text(encoding="utf-8")
    text = "# my home, edited by hand\n" + ref.replace('outdoor = "backyard"\n', 'outdoor = "backyard"\noutdoor_id = 5  # old\n')
    out = set_monitor_keys(text, {"indoor": {"indoor_id": 7, "indoor_host": None},
                                  "outdoor": {"outdoor_id": 9, "outdoor_serial": "aabbccddeeff"}})
    old, new = text.splitlines(), out.splitlines()
    assert new.index("indoor_id = 7") == new.index('indoor = "apartment"') + 1
    assert new.index("outdoor_id = 9") == new.index('outdoor = "backyard"') + 1
    assert new.index('outdoor_serial = "aabbccddeeff"') == new.index("outdoor_id = 9") + 1
    added = {"indoor_id = 7", "outdoor_id = 9", 'outdoor_serial = "aabbccddeeff"'}
    assert [l for l in new if l not in added] == [l for l in old if not l.startswith("outdoor_id = 5")]
    assert "indoor_id" not in set_monitor_keys(out, {"indoor": {"indoor_id": None}})   # None removes the line
    assert set_monitor_keys('[home]\ntemp_unit = "F"\n', {"indoor": {"indoor_serial": "aabbccddeeff"}}) \
        == '[home]\ntemp_unit = "F"\n\n[place]\nindoor_serial = "aabbccddeeff"\n'


def test_backup_config_copies_and_keeps_the_newest_five(tmp_path):
    """A backup is what the household's undo rests on: same bytes and mode, and the oldest go, not the newest."""
    import os
    from config import BACKUPS_KEPT, backup_config
    cfg = tmp_path / "config.toml"
    made = []
    for n in range(BACKUPS_KEPT + 2):
        cfg.write_text(f"v = {n}\n")
        cfg.chmod(0o600)
        os.utime(cfg, (1_500_000_000, 1_500_000_000))   # an old modified time must not decide which backup is newest
        made.append(backup_config(cfg))
    left = sorted(tmp_path.glob("config.toml.bak-*"))
    assert len(left) == BACKUPS_KEPT and set(left) == set(made[-BACKUPS_KEPT:])
    assert made[-1].read_text() == f"v = {BACKUPS_KEPT + 1}\n" and made[-1].stat().st_mode & 0o777 == 0o600


def test_local_timezone_prefers_tz_then_the_localtime_link_then_utc(tmp_path):
    # setup writes this into a new home: a wrong name would fail every load, so a bad TZ must fall through.
    from config import local_timezone
    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/America/Chicago")
    assert local_timezone({"TZ": "Europe/Paris"}, link) == "Europe/Paris"
    assert local_timezone({"TZ": "Not/AZone"}, link) == "America/Chicago"
    assert local_timezone({}, tmp_path / "missing") == "UTC"


def test_a_home_without_a_place_is_valid_but_half_a_place_is_not(tmp_path):
    """Setup writes only the timezone; a name or one coordinate alone is a typo, not a home without a place."""
    from config import located
    cfg = load_config({"CONFIG_TOML": '[place]\ntimezone = "UTC"\nindoor = "Home"\nindoor_serial = "84fce602549c"\n'}, tmp_path)
    assert not located(cfg) and cfg.place.lat is None and cfg.outdoor_from is None
    view = public_view(cfg)
    assert view["place"]["lat"] is None and view["links"] == {}
    with pytest.raises(ConfigError) as e:
        load_config({"CONFIG_TOML": '[place]\nlat = 40.7\ntimezone = "UTC"\n'}, tmp_path)
    assert "place.name, place.lat and place.lon go together" in str(e.value)
