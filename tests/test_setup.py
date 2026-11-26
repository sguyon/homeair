"""Setup uses invented keys, places and monitors. No network or listening ports."""
import hashlib
import json
import os
import re
import stat
import sys
import threading
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import road_data

from config import DEFAULT_LIMITS, load_config, monitors as config_monitors

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import setup
import localnet
from scripts import fake_airgradient as fake

KM_LAT = 1 / 111.2   # degrees of latitude per km
DENVER = (39.7403115, -104.9753154)


def osm(lat, lon, name="", category="building", **address):
    return {"lat": str(lat), "lon": str(lon), "category": category, "name": name, "address": address}


US = {"country": "United States", "country_code": "us"}
OGDEN = [osm(*DENVER, "Ogden Theatre", "amenity", amenity="Ogden Theatre", house_number="935", road="East Colfax Avenue",
             neighbourhood="Swallow Hill Historic District", city="Denver", state="Colorado", postcode="80218",
             **{"ISO3166-2-lvl4": "US-CO"}, **US)]
TIMES_SQUARE = [
    osm(40.7570095, -73.9859724, "Times Square", "highway", road="Times Square", neighbourhood="Manhattan Community Board 5",
        suburb="Manhattan", city="New York", state="New York", postcode="10036", **{"ISO3166-2-lvl4": "US-NY"}, **US),
    osm(40.7572614, -73.9858998, "Times Square", "place", square="Times Square", suburb="Manhattan", city="New York",
        state="New York", postcode="10036", **{"ISO3166-2-lvl4": "US-NY"}, **US)]
FIFTH_AVE = [osm(40.7484421, -73.9856589, "", "building", house_number="350", road="5th Avenue", neighbourhood="Koreatown",
                 suburb="Manhattan", city="New York", state="New York", postcode="10118", **{"ISO3166-2-lvl4": "US-NY"}, **US)]
BACK_BAY = [osm(42.3503, -71.0753, "", "building", house_number="200", road="Clarendon Street", neighbourhood="Copley",
                suburb="Back Bay", city="Boston", state="Massachusetts", postcode="02116-5021",
                **{"ISO3166-2-lvl4": "US-MA"}, **US)]
MARAIS = [osm(48.8589, 2.3618, "Le Marais", "place", suburb="Le Marais", city="Paris", state="Ile-de-France",
              postcode="75004", country="France", country_code="fr", **{"ISO3166-2-lvl4": "FR-IDF"})]
TWO_STATES = [osm(41.92, -87.70, "Logan Square", "place", suburb="Logan Square", city="Chicago", state="Illinois",
                  **{"ISO3166-2-lvl4": "US-IL"}, **US),
              osm(39.95, -75.17, "Logan Square", "place", suburb="Logan Square", city="Philadelphia", state="Pennsylvania",
                  **{"ISO3166-2-lvl4": "US-PA"}, **US)]

ORIGINAL_HTTP_GET = setup.http_get_json
ORIGINAL_ACCOUNT_ROWS = setup.account_rows
TOKEN = "invented-account-token"
NOW = datetime.now(timezone.utc).isoformat()
INDOOR = {"locationId": 7001, "locationType": "indoor", "locationName": "Bedroom", "timestamp": NOW}
OUTDOOR = {"locationId": 7002, "locationType": "outdoor", "locationName": "Porch", "timestamp": NOW}


@pytest.fixture
def root(tmp_path, monkeypatch):
    def no_network(url, **k):
        if url.endswith("/health"):   # --status asks the running page; none is running here
            raise requests.ConnectionError("not running")
        if url.startswith(road_data.ROADS_BASE):   # the data host: not reachable here (the roads tests serve it)
            raise requests.ConnectionError("no data host in tests")
        pytest.fail("Unexpected network request")
    monkeypatch.setattr(setup.requests, "get", no_network)

    def no_browse(seconds, entries):
        raise OSError("no network in tests")   # discovery then finds nothing at once instead of browsing the real Wi-Fi
    monkeypatch.setattr(localnet, "_zeroconf_browse", no_browse)
    monkeypatch.setattr(setup, "account_rows", lambda token, **k: [dict(INDOOR)])
    monkeypatch.setattr(setup.publicmap, "fetch_area", lambda *a, **k: [])
    monkeypatch.setattr(setup.check_config.publicmap, "fetch_area", lambda *a, **k: [])
    def geo(url, params):
        if url == setup.NOMINATIM_SEARCH:
            return OGDEN
        if url == setup.NOMINATIM_REVERSE:
            return OGDEN[0]
        assert url == setup.OPEN_METEO_FORECAST
        return {"timezone": "America/Denver"}
    monkeypatch.setattr(setup, "http_get_json", geo)
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    """Bare setup's steps after finding the home, recorded instead of run: no app, login item, skill link or browser."""
    seen = []
    def fake(name, result=0):
        def step(*a, **k):
            seen.append(name)
            return result
        return step
    for name in ("start_app", "start_at_login", "agent_skill", "open_page", "live"):
        monkeypatch.setattr(setup, name, fake(name))
    return seen


def run(root, capsys, *args, env=None):
    code = setup.main(list(args), {"AIRGRADIENT_TOKEN": TOKEN} if env is None else env, root)
    output = capsys.readouterr()
    return code, output.out + output.err


def home(root):
    return load_config({}, root)


def without_push_keys(path):
    """The .env text minus the alert keys setup also makes (they are random)."""
    return "".join(l + "\n" for l in path.read_text().splitlines() if not l.startswith("VAPID_"))


def test_default_command_prepares_editor_file(root, capsys, calls):
    code, out = run(root, capsys, env={})
    assert code == 0 and "API token" in out   # with nothing found, the token is the way in (on the page)
    assert without_push_keys(root / ".env") == "AIRGRADIENT_TOKEN=\n"
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600
    assert "Connect AirGradient on the page instead." not in out   # the unclear old line; "Nothing is read yet" says it


@pytest.mark.parametrize("text", ["# keep\nOTHER=x\n", "export AIRGRADIENT_TOKEN=\nOTHER=x\n", f"AIRGRADIENT_TOKEN={TOKEN}\nOTHER=x\n"])
def test_editor_preparation_keeps_existing_lines_and_values(root, capsys, calls, text):
    path = root / ".env"
    path.write_text(text)
    code, out = run(root, capsys, env={})
    assert code == 0 and TOKEN not in out
    expected = text if "AIRGRADIENT_TOKEN=" in text else text + "AIRGRADIENT_TOKEN=\n"
    assert without_push_keys(path) == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_without_a_token_the_address_goes_into_the_wifi_config(root, capsys):
    # The released setup needed the token first; the Wi-Fi setup has none: the place and profiles join its config.toml,
    # its monitors stay, the exact location stays here (for the roads) and the page still gets it rounded.
    (root / "config.toml").write_text(f'[place]\ntimezone = "America/Denver"\nindoor = "home"\nindoor_serial = "{IN_SERIAL}"\n\n'
                                      '[profiles]\ntoddler = false\nsinus = false\nrunner = false\n')
    code, out = run(root, capsys, "--address", "Denver", "--profiles", "runner", env={})
    assert code == 0, out
    cfg = written(root)
    assert cfg["place"]["indoor_serial"] == IN_SERIAL and cfg["profiles"]["runner"] is True
    assert cfg["place"]["lat"] == round(float(OGDEN[0]["lat"]), setup.EXACT_DECIMALS)
    assert home(root).place.lat == round(float(OGDEN[0]["lat"]), 2)   # what leaves the computer and shows on the page
    code, out = run(root, capsys, "--profiles", "none", env={})
    assert code == 0 and written(root)["place"]["name"] == cfg["place"]["name"]   # profiles alone keep the place
    assert written(root)["profiles"]["runner"] is False


def test_railway_gets_the_location_rounded(root, monkeypatch, capsys):
    # The exact location never leaves this computer: Home Air online is public, with no sign-in.
    (root / "config.toml").write_text('[place]\nname = "Ogden"\nlat = 39.7403\nlon = -104.9753\ntimezone = "America/Denver"\n')
    (root / ".env").write_text(f"AIRGRADIENT_TOKEN={TOKEN}\n")
    sent = {}
    monkeypatch.setattr(setup.subprocess, "run", lambda args, **k: sent.update({args[3]: k["input"]}))
    assert run(root, capsys, "railway", "--service", "home-air")[0] == 0
    assert "lat = 39.74\n" in sent["CONFIG_TOML"] and "lon = -104.98\n" in sent["CONFIG_TOML"]


def test_small_config_defaults_and_single_resolution(root, monkeypatch, capsys):
    seen = []
    real = setup.http_get_json
    monkeypatch.setattr(setup, "http_get_json", lambda u, p: seen.append((u, p)) or real(u, p))
    calls = []
    monkeypatch.setattr(setup, "account_rows", lambda token: calls.append(token) or [INDOOR])
    code, out = run(root, capsys, "--address", "Ogden Theatre, Denver", "--profiles", "sinus,runner")
    assert code == 0, out
    assert [u for u, p in seen] == [setup.NOMINATIM_SEARCH, setup.OPEN_METEO_FORECAST]
    assert seen[0][1]["q"] == "Ogden Theatre, Denver"
    assert seen[1][1] == {"latitude": 39.74, "longitude": -104.98, "timezone": "auto", "forecast_days": 1}
    assert calls == [TOKEN]  # discovery and the live check share one account reading
    cfg = home(root)
    assert cfg.place.name == "Denver" and cfg.place.timezone == "America/Denver"
    assert cfg.place.indoor == "Bedroom" and cfg.place.indoor_id == 7001
    assert cfg.place.outdoor is None and cfg.outdoor_from == "model"
    assert cfg.appliances == () and cfg.profiles == {"toddler": False, "sinus": True, "runner": True}
    assert cfg.limits == DEFAULT_LIMITS and cfg.road is None
    raw = tomllib.loads((root / "config.toml").read_text())
    assert set(raw) == {"place", "profiles", "sources", "home"}
    assert cfg.temp_unit == "F" and "Temperatures in °F" in out   # a US home starts in °F (decision 8), and setup says so
    assert "estimate" in out and "Config OK" in out and out.rstrip().endswith("Live OK")
    assert "Ogden Theatre" not in out and TOKEN not in out


def test_ambiguous_monitors_ask_with_candidates_and_flag(root, monkeypatch, capsys):
    rows = [INDOOR, {**INDOOR, "locationId": 7003, "locationName": "Kitchen"}, OUTDOOR]
    monkeypatch.setattr(setup, "account_rows", lambda token: rows)
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 1 and "Choose one of:" in out and "Bedroom" in out and "Kitchen" in out and "--indoor-id" in out
    assert not (root / "config.toml").exists()
    code, out = run(root, capsys, "--address", "Denver", "--indoor-id", "7003")
    assert code == 0, out
    assert home(root).place.indoor_id == 7003 and home(root).place.outdoor_id == 7002
    assert 'indoor "Kitchen"' in out


@pytest.mark.parametrize("rows", [[], [{"locationId": 1, "locationName": "Unknown"}]])
def test_empty_or_unclassified_account_does_not_guess(root, monkeypatch, capsys, rows):
    monkeypatch.setattr(setup, "account_rows", lambda token: rows)
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 1 and "AirGradient" in out and not (root / "config.toml").exists()


def test_unclassified_monitor_requires_an_explicit_choice(root, monkeypatch, capsys):
    row = {**INDOOR, "locationType": None}
    monkeypatch.setattr(setup, "account_rows", lambda token: [row])
    code, out = run(root, capsys, "--address", "Denver", "--indoor-id", "7001")
    assert code == 0, out
    assert home(root).place.indoor_id == 7001


def test_wrong_monitor_choice_is_refused(root, capsys):
    code, out = run(root, capsys, "--address", "Denver", "--indoor-id", "999")
    assert code == 1 and "Choose one of:" in out and not (root / "config.toml").exists()


def test_public_lookup_failure_is_an_estimate(root, monkeypatch, capsys):
    def down(*a, **k):
        raise requests.ConnectionError(TOKEN)
    monkeypatch.setattr(setup.publicmap, "fetch_area", down)
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 0 and "estimate" in out and TOKEN not in out
    assert home(root).outdoor_from == "model"


def test_accepts_public_source_suggestion(root, monkeypatch, capsys):
    monkeypatch.setattr(setup.publicmap, "fetch_area", lambda *a, **k: [{"locationId": 9001, "model": "O-1PST", "latitude": 39.74, "longitude": -104.98, "timestamp": NOW}])
    monkeypatch.setattr(setup.publicmap, "suggest", lambda *a: (9001, None, 1))
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 0, out
    assert home(root).outdoor_from == 9001


def test_public_source_requests_rounded_place_but_ranks_from_exact(monkeypatch):
    # The exact home is east of the rounded centre: rounding the ranking would pick 9002 instead.
    place = setup.Place(40.7049, -73.9951, "Test home", "test", "test")
    monitors = [
        {"locationId": 9001, "latitude": 40.7049, "longitude": -73.993, "measuredAt": NOW},
        {"locationId": 9002, "latitude": 40.7, "longitude": -74.0, "measuredAt": NOW},
        # Inside the old exact-centred 16 km box, outside an unpadded rounded-centred 16 km box.
        {"locationId": 9003, "latitude": 40.8485, "longitude": -73.9951, "measuredAt": NOW},
    ]
    seen = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{**m, "dataSource": "AirGradient", "pm25": 4} for m in monitors
                    if seen[-1]["ymin"] <= m["latitude"] <= seen[-1]["ymax"]
                    and seen[-1]["xmin"] <= m["longitude"] <= seen[-1]["xmax"]]

    def get(url, source, **kwargs):
        assert url == setup.publicmap.AREA_URL and source == "ag_map_area"
        seen.append(kwargs["params"])
        return Resp()

    monkeypatch.setattr(setup.publicmap.upstream, "get", get)
    real_suggest = setup.publicmap.suggest

    def suggest(world, lat, lon, now):
        assert {m["locationId"] for m in world} == ({9001, 9002, 9003} if lat == 40.7049 else {9001, 9002})
        return real_suggest(world, lat, lon, now)

    monkeypatch.setattr(setup.publicmap, "suggest", suggest)
    assert setup.outdoor_source(place) == 9001
    params = seen[0]
    assert (params["ymin"] + params["ymax"]) / 2 == pytest.approx(40.7)
    assert (params["xmin"] + params["xmax"]) / 2 == pytest.approx(-74.0)
    assert params["ymax"] >= 40.8485
    # Radius must also depend only on the rounded centre, never the exact position within its cell.
    assert setup.outdoor_source(setup.Place(40.7001, -73.9999, "Test home", "test", "test")) == 9002
    assert seen[1] == params
    # Near the pole, box()'s longitude floor puts a box-edge monitor within the 3 km source limit.
    # It is inside the old exact box (east edge -59.62212), outside the rounded unpadded one (-59.62702).
    monitors[:] = [{"locationId": 9003, "latitude": 89.9049, "longitude": -59.623, "measuredAt": NOW}]
    monkeypatch.setattr(setup.publicmap, "suggest", real_suggest)
    assert setup.outdoor_source(setup.Place(89.9049, -73.9951, "Test home", "test", "test")) == 9003
    assert (seen[2]["xmin"] + seen[2]["xmax"]) / 2 == pytest.approx(-74.0)
    assert seen[2]["xmax"] > -59.623


def test_existing_config_is_copied_before_replacement(root, capsys):
    path = root / "config.toml"
    path.write_text("old config\n")
    path.chmod(0o600)
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 0, out
    backup = next(root.glob("config.toml.bak-*"))
    assert backup.read_text() == "old config\n" and str(backup) in out
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 0 and len(list(root.glob("config.toml.bak-*"))) == 1


def test_the_home_command_keeps_the_monitors_found_on_the_wifi(root, monkeypatch, capsys):
    # --address rebuilds config.toml: without its serial a monitor found on the Wi-Fi silently goes back to the cloud
    # (the poller reads only monitors with a serial). Only the account's same monitor keeps it.
    (root / "config.toml").write_text(f'[place]\ntimezone = "America/Denver"\nindoor = "home"\nindoor_serial = "{IN_SERIAL}"\n'
                                      f'outdoor = "outdoor"\noutdoor_serial = "{OUT_SERIAL}"\n')
    rows = [{**INDOOR, "serialno": IN_SERIAL.upper()}, {**OUTDOOR, "serialno": "84fce6999999"}]
    monkeypatch.setattr(setup, "account_rows", lambda token: rows)
    code, out = run(root, capsys, "--address", "Denver", "--profiles", "runner")
    assert code == 0, out
    place = written(root)["place"]
    assert (place["indoor_id"], place["indoor_serial"]) == (7001, IN_SERIAL)
    assert place["outdoor_id"] == 7002 and "outdoor_serial" not in place   # the account's outdoor monitor is another one
    assert written(root)["profiles"]["runner"] is True


def test_a_config_that_isnt_toml_is_one_plain_line(root, capsys, monitors, calls):
    # Setup run again over a hand-broken config.toml must say so, not print a traceback, and leave the file alone.
    (root / "config.toml").write_text("[place\nindoor = \n")
    code, out = run(root, capsys, env=monitors((IN_SERIAL, "I-9PSL", "3.7.1", None)))
    assert code == 1 and "config.toml can't be read" in out and "Traceback" not in out
    assert out.strip().splitlines()[-1].startswith("config.toml can't be read")
    assert (root / "config.toml").read_text() == "[place\nindoor = \n" and not list(root.glob("config.toml.bak-*"))


@pytest.mark.parametrize("results,country,smoke,zip_code,name,unit", [(MARAIS, "FR", False, None, "Le Marais", "C"),
                                                                     (BACK_BAY, "US", True, "02116", "Back Bay", "F")])
def test_geographic_corrections(root, monkeypatch, capsys, results, country, smoke, zip_code, name, unit):
    def geo(url, params):
        return results if url == setup.NOMINATIM_SEARCH else {"timezone": "Europe/Paris" if country == "FR" else "America/New_York"}
    monkeypatch.setattr(setup, "http_get_json", geo)
    code, out = run(root, capsys, "--address", name)
    assert code == 0, out
    cfg = home(root)
    assert cfg.sources["smoke"] is smoke and cfg.place.pollen_zip == zip_code and cfg.place.name == name
    assert cfg.pollen_seasons == ()
    assert cfg.temp_unit == unit and f"Temperatures in °{unit}" in out


def test_one_nominatim_request_with_user_agent_and_no_second_geocoder(root, monkeypatch, capsys):
    seen = []
    class Reply:
        def raise_for_status(self):
            pass
        def json(self):
            return []
    def get(url, **kwargs):
        seen.append((url, kwargs))
        return Reply()
    monkeypatch.setattr(setup.requests, "get", get)
    monkeypatch.setattr(setup, "http_get_json", ORIGINAL_HTTP_GET)
    code, out = run(root, capsys, "--address", "Missing place")
    assert code == 1 and "Couldn't find that address" in out
    assert len(seen) == 1 and seen[0][0] == setup.NOMINATIM_SEARCH
    assert seen[0][1]["headers"]["User-Agent"] == setup.USER_AGENT and setup.USER_AGENT.startswith("HomeAir/")


def test_stale_monitor_fails_live_check_after_writing(root, monkeypatch, capsys):
    monkeypatch.setattr(setup, "account_rows", lambda token: [{**INDOOR, "timestamp": (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}])
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 1 and "no recent reading" in out and (root / "config.toml").exists()


def test_account_request_is_read_only_and_errors_never_expose_keys(root, monkeypatch, capsys):
    monkeypatch.setattr(setup, "account_rows", ORIGINAL_ACCOUNT_ROWS)
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        raise requests.ConnectionError(TOKEN)
    monkeypatch.setattr(setup.requests, "get", get)
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 1 and TOKEN not in out and "AirGradient" in out
    assert len(calls) == 1 and calls[0][1]["params"] == {"token": TOKEN}
    assert calls[0][1]["allow_redirects"] is False


def test_railway_uses_stdin_and_prints_only_names(root, monkeypatch, capsys):
    config = '[place]\nname = "Private home"\n'
    (root / "config.toml").write_text(config)
    (root / ".env").write_text(f'AIRGRADIENT_TOKEN="{TOKEN}"\nGOOGLE_POLLEN_API_KEY=invented-pollen-key\nOTHER=ignored\n')
    fake = root / "railway"
    fake.write_text("#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\np=Path(os.environ['RAILWAY_CAPTURE'])\nwith p.open('a') as f: f.write(json.dumps({'args':sys.argv[1:], 'value':sys.stdin.read()})+'\\n')\nprint('should never appear')\n")
    fake.chmod(0o755)
    capture = root / "capture.jsonl"
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("RAILWAY_CAPTURE", str(capture))
    code, out = run(root, capsys, "railway", "--service", "home-air", env={})
    assert code == 0, out
    sent = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [s['value'] for s in sent] == [config, TOKEN, "invented-pollen-key"]
    assert [s['args'] for s in sent] == [["variable", "set", name, "--stdin", "--service", "home-air", "--skip-deploys"]
                                         for name in ("CONFIG_TOML", "AIRGRADIENT_TOKEN", "GOOGLE_POLLEN_API_KEY")]
    assert TOKEN not in out and "invented-pollen-key" not in out and "Private home" not in out and "should never appear" not in out
    assert all(name in out for name in ("CONFIG_TOML", "AIRGRADIENT_TOKEN", "GOOGLE_POLLEN_API_KEY"))
    # No deploy starts per variable: the last line says how to deploy with all of them.
    assert "railway up --service home-air --detach" in out and "railway redeploy --service home-air" in out
    assert "ask the user first" in out


def test_railway_sends_no_monitor_address_and_needs_the_token(root, monkeypatch, capsys):
    """A Home Air online reads the cloud only: no host line leaves this computer, and without the token nothing is sent."""
    config = '[place]\nname = "Home"\nindoor_serial = "84fce6000001"\nindoor_host = "192.168.1.20"\noutdoor_host = "10.0.0.7"\n'
    (root / "config.toml").write_text(config)
    (root / ".env").write_text(f"AIRGRADIENT_TOKEN={TOKEN}\n")
    sent = []
    def record(args, **kwargs):
        sent.append((args, kwargs["input"]))
        return setup.subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(setup.subprocess, "run", record)
    code, out = run(root, capsys, "railway", "--service", "home-air")
    assert code == 0, out
    by_name = {args[3]: value for args, value in sent}
    assert "_host" not in by_name["CONFIG_TOML"] and "192.168" not in by_name["CONFIG_TOML"] and "84fce6000001" in by_name["CONFIG_TOML"]
    assert by_name["AIRGRADIENT_TOKEN"] == TOKEN and TOKEN not in out
    assert "--stdin" in sent[0][0] and TOKEN not in " ".join(sent[0][0])
    sent.clear()
    (root / ".env").write_text("AIRGRADIENT_TOKEN=\n")
    code, out = run(root, capsys, "railway", "--service", "home-air")
    assert code == 1 and setup.NO_WEB_WITHOUT_TOKEN in out and sent == []


def test_railway_omits_empty_keys_and_suppresses_cli_errors(root, monkeypatch, capsys):
    (root / "config.toml").write_text("# private config\n")
    (root / ".env").write_text(f'AIRGRADIENT_TOKEN={TOKEN}\nGOOGLE_POLLEN_API_KEY=your_key_here\n')
    calls = []
    def fail(args, **kwargs):
        calls.append((args, kwargs))
        raise setup.subprocess.CalledProcessError(1, args, output=TOKEN, stderr=TOKEN)
    monkeypatch.setattr(setup.subprocess, "run", fail)
    code, out = run(root, capsys, "railway", "--service", "home-air")
    assert code == 1 and TOKEN not in out and len(calls) == 1



def test_saved_monitor_ids_filter_the_page_readings(root, monkeypatch, capsys):
    import sources
    from types import SimpleNamespace
    run(root, capsys, "--address", "Denver")
    cfg = home(root)
    rows = [INDOOR, {**INDOOR, "locationId": 999}, OUTDOOR]
    monkeypatch.setattr(sources, "runtime", SimpleNamespace(CFG=cfg))
    assert sources.place_rows(rows) == [INDOOR]
    assert len(rows) == 3


def test_a_portable_go_is_never_taken_as_the_home_monitor(monkeypatch):
    """An AirGradient Go (model P-…) moves with its owner: an account that has one, its place set to indoor, must not
    give the home a second indoor monitor (an older config without ids picks by type), unless its id was chosen."""
    import sources
    from types import SimpleNamespace
    go = {"locationId": 7009, "locationType": "indoor", "locationName": "Go", "model": "P-1PSG", "timestamp": NOW}
    untyped_go = {**go, "locationId": 7010, "locationType": None}
    assert sources.choose_monitors([INDOOR, go, OUTDOOR]) == {"indoor": INDOOR, "outdoor": OUTDOOR}
    assert sources.choose_monitors([INDOOR, go, OUTDOOR], indoor_id=7009)["indoor"]["locationId"] == 7009
    place = SimpleNamespace(indoor="Bedroom", outdoor="Porch", indoor_id=None, outdoor_id=None)
    monkeypatch.setattr(sources, "runtime", SimpleNamespace(CFG=SimpleNamespace(demo=False, place=place)))
    assert sources.place_rows([INDOOR, go, untyped_go, OUTDOOR]) == [INDOOR, OUTDOOR]
    # an older home whose only indoor monitor is a Go keeps reading it, as before
    assert sources.place_rows([go, OUTDOOR]) == [go, OUTDOOR]
    # setup with a Go as the only indoor monitor names it and the flag to choose it, never "no monitors"
    for rows in ([go], [go, OUTDOOR]):
        with pytest.raises(sources.MonitorChoice, match="--indoor-id") as err:
            sources.choose_monitors(rows)
        assert "Go" in str(err.value) and "7009" in str(err.value)


def test_non_integer_monitor_ids_are_invalid(root):
    from config import ConfigError
    text = '[place]\nname="Home"\nlat=40\nlon=-74\ntimezone="America/New_York"\nindoor="Room"\nindoor_id="bad"\n[sources]\noutdoor_from="model"\n'
    with pytest.raises(ConfigError, match="indoor_id"):
        load_config({"CONFIG_TOML": text}, root)


def test_unknown_outdoor_can_be_chosen_without_inventing_an_indoor_monitor(root, monkeypatch, capsys):
    monkeypatch.setattr(setup, "account_rows", lambda token: [{**OUTDOOR, "locationType": None}])
    code, out = run(root, capsys, "--address", "Denver", "--outdoor-id", "7002")
    assert code == 0, out
    assert home(root).place.indoor is None and home(root).place.outdoor_id == 7002


@pytest.mark.parametrize("args,words", [
    (["--lat", "40"], "go together"),
    (["--timezone", "Wrong/Zone"], "timezone name"),
    (["--zip", "123"], "5 digits"),
])
def test_lookup_corrections_are_validated(root, capsys, args, words):
    code, out = run(root, capsys, "--address", "Denver", *args)
    assert code == 2 and words in out and not (root / "config.toml").exists()


def test_ambiguous_places_still_require_city(root, monkeypatch, capsys):
    monkeypatch.setattr(setup, "http_get_json", lambda *a: TWO_STATES)
    code, out = run(root, capsys, "--address", "Logan Square")
    assert code == 1 and "Several places match" in out and "Illinois" in out and "Pennsylvania" in out


def test_geocoder_outage_is_plain_and_does_not_fall_back(root, monkeypatch, capsys):
    calls = []
    def down(url, params):
        calls.append(url)
        raise requests.ConnectionError("down")
    monkeypatch.setattr(setup, "http_get_json", down)
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 1 and "Couldn't reach OpenStreetMap" in out and calls == [setup.NOMINATIM_SEARCH]


@pytest.mark.parametrize("rows,token,expected,words", [
    ([INDOOR, OUTDOOR], True, 0, "Live OK"),
    ([{**INDOOR, "locationId": 999}, OUTDOOR], True, 1, "no indoor monitor"),
    ([{**INDOOR, "timestamp": None}, OUTDOOR], True, 1, "no recent reading"),
    ([INDOOR, OUTDOOR], False, 1, "add AIRGRADIENT_TOKEN to .env"),
])
def test_live_checks_running_destination_using_only_gets(root, monkeypatch, capsys, rows, token, expected, words):
    from config import public_view
    monkeypatch.setattr(setup, "account_rows", lambda token: [INDOOR, OUTDOOR])
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 0, out
    app = public_view(home(root), root)
    calls = []
    class Reply:
        status_code = 200
        def __init__(self, value):
            self.value = value
            self.text = f'<script>window.APP = {json.dumps(value)};</script>'
        def json(self):
            return self.value
    def get(url, **kwargs):
        calls.append(url)
        return Reply({"status": "ok", "airgradient_token_set": token} if url.endswith("/health") else rows if url.endswith("/api/current") else app)
    monkeypatch.setattr(setup.requests, "get", get)
    for verb in ("post", "put", "patch", "delete"):
        monkeypatch.setattr(setup.requests, verb, lambda *a, **k: pytest.fail("Only GET is allowed"))
    code, out = run(root, capsys, "--live", "http://destination:8000")
    assert code == expected and words in out, out
    assert calls == ["http://destination:8000/health", "http://destination:8000/"] + (["http://destination:8000/api/current"] if token else [])


@pytest.mark.parametrize("monitors,token,expected,words", [
    ({"indoor": "local", "outdoor": "local"}, False, 0, "Note: optional: charts start from now"),
    ({"indoor": "local", "outdoor": "stale"}, False, 1, 'outdoor "Porch": not answering'),
])
def test_live_says_where_each_monitor_is_read_from(root, monkeypatch, capsys, monitors, token, expected, words):
    from config import public_view
    monkeypatch.setattr(setup, "account_rows", lambda token: [INDOOR, OUTDOOR])
    code, out = run(root, capsys, "--address", "Denver")
    assert code == 0, out
    app = public_view(home(root), root)
    class Reply:
        status_code = 200
        def __init__(self, value):
            self.value = value
            self.text = f'<script>window.APP = {json.dumps(value)};</script>'
        def json(self):
            return self.value
    health = {"status": "ok", "airgradient_token_set": token, "monitors": monitors}
    monkeypatch.setattr(setup.requests, "get", lambda url, **k: Reply(health if url.endswith("/health") else app))
    code, out = run(root, capsys, "--live", "http://destination:8000")
    assert code == expected and words in out, out
    if expected == 0:
        assert "Live OK" in out and out.count(": on your Wi-Fi") == 2, out


def test_after_the_page_connects_the_default_command_names_the_monitors(root, monkeypatch, capsys, calls):
    # The agent tells the user what the account has, without ever seeing the token.
    monkeypatch.setattr(setup, "account_rows", lambda token: [{"locationName": "Living room", "locationType": "indoor"},
                                                                {"locationName": "Back porch", "model": "O-1PST"}])
    code, out = run(root, capsys)
    assert code == 0 and TOKEN not in out
    assert "Connected to AirGradient: 2 monitors: Living room (indoor), Back porch (outdoor)." in out


# --status: what's done and the one next command. Read-only: no file written, no question, exit code 0.

def files(root):
    return {p.relative_to(root): (p.read_bytes(), p.stat().st_mode) for p in root.rglob("*") if p.is_file()}


def status(root, capsys, env=None):
    before = files(root)
    code, out = run(root, capsys, "--status", env=env)
    assert code == 0, out
    assert files(root) == before, "--status must not change any file"
    return out


def next_line(out):
    lines = [l for l in out.splitlines() if l.startswith("Next: ")]
    assert len(lines) == 1, out
    return lines[0].removeprefix("Next: ")


def test_status_on_an_empty_folder_names_the_first_command(root, capsys):
    out = status(root, capsys, env={"HOME": str(root)})
    assert next_line(out) == setup.SETUP_CMD
    assert "A first look" in out and "Your home" in out and "On a home server or online" in out and not (root / ".env").exists()


def test_status_without_a_place_points_to_the_home_questions(root, capsys, monitors, calls):
    # The first look done, the home's questions not: the way back is written out, never left to memory.
    run(root, capsys, env=monitors((IN_SERIAL, "I-9PSL", "3.7.1", None)))
    out = status(root, capsys, env={"HOME": str(root)})
    assert "Address: not set" in out and "set up my home in Home Air" in out
    assert "Home Air online needs: the AirGradient API token and the address" in out


def test_status_on_a_complete_home_needs_nothing_and_never_prints_keys(root, capsys):
    run(root, capsys, "--address", "Denver", "--profiles", "runner")
    (root / ".env").write_text(f'AIRGRADIENT_TOKEN="{TOKEN}"\n')
    out = status(root, capsys, env={"HOME": str(root), "GOOGLE_POLLEN_API_KEY": "invented-pollen-key"})
    assert "Who lives here: runner" in out and "API token: added" in out and "Home Air online: ready" in out
    assert next_line(out).startswith("nothing needed") or setup.START_CMD in next_line(out)
    assert TOKEN not in out and "invented-pollen-key" not in out


def test_status_reports_a_broken_config_in_plain_words(root, capsys):
    (root / "config.toml").write_text('[place]\nname = "Home"\n')
    out = status(root, capsys, env={"HOME": str(root)})
    assert "config.toml" in out and "lat" in out and next_line(out) == setup.SETUP_CMD


LAST_MESSAGE = ["**Home Air is running at http://localhost:5555**",
                "`./homeair setup` opened the page in your browser.",
                "Both monitors are read on your Wi-Fi:",
                "- **Living room** (indoor): CO₂ is 650 ppm, under the 800 ppm limit.",
                "- **Balcony** (outdoor): PM2.5 is 4 µg/m³, under the 15 µg/m³ limit.",
                "Home Air is in your Mac's Login Items, so it starts again each time you turn on this Mac and sign in.",
                "Next come 4 optional steps (about 4 min). To stop at any step and do the rest later, reply **skip**."]


def doc(name):
    return (Path(__file__).resolve().parent.parent / name).read_text()


def section(text, title):
    return text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def test_setup_md_last_message_is_the_decided_text():
    # where-it-runs spec, Decided 2 (Sacha's edits, 14 Nov): the address plain on its own line, no tab claim, no log, no
    # start command
    again = section(doc("SETUP.md"), "3. Tell the user it's ready")
    quoted = [line[2:] for line in again.splitlines() if line.startswith("> ")]   # mock 111, picked 24 Nov
    assert [q for q in quoted if q] == LAST_MESSAGE
    message = "\n".join(quoted)
    assert "tab" not in message and "app.log" not in message and "run.sh" not in message


def test_setup_md_says_what_each_offer_runs():
    later = section(doc("SETUP.md"), "5. On a home server or online")
    assert "./homeair start-at-login on" in section(doc("SETUP.md"), "Commands for later")
    assert "./homeair home-server" in later and "DEPLOYMENT.md" in later and "skill add --url" in later


def test_setup_md_has_no_question_about_where_it_runs():
    # mock 97: B, then "statement is better": the last message says what's possible; no question first
    assert "Where should the page run" not in doc("SETUP.md")
    rules = doc("AGENTS.md")
    assert "- No new questions. The first look" in rules and "beyond where the page runs" not in rules


def test_setup_md_commands_use_the_launcher():
    for name in ("SETUP.md", "DEPLOYMENT.md"):
        assert "uv run --python 3.12" not in doc(name), name
    assert "./homeair setup" in doc("SETUP.md") and "scripts/setup.py" not in doc("SETUP.md")


def test_cloud_sandbox_stops_setup_but_not_development():
    ready = section(doc("SETUP.md"), "1. Get Home Air")
    assert "Claude Code on the web" in ready and "Codex cloud" in ready and "stop" in ready
    assert "AGENTS.md" in ready


def test_no_straight_to_railway_path():
    for name in ("SETUP.md", "DEPLOYMENT.md"):
        assert "without a local run" not in doc(name), name


def test_start_survives_the_session():
    assert "./homeair start" in section(doc("SETUP.md"), "Commands for later")


def test_deployment_md_says_which_hosts_are_tested():
    # Sacha, 2026-11-13: "we dont have anything that says we don't support other methods like other hosting providers besides railway"
    intro = (Path(__file__).resolve().parent.parent / "DEPLOYMENT.md").read_text().split("\n## ")[0]
    assert "Only Railway and Docker are tested" in intro
    assert "`Dockerfile`" in intro and "untested" in intro


def test_wait_token_reports_the_account_as_soon_as_the_page_saves_the_token(root, capsys, monkeypatch):
    # Sacha, 2026-11-13: "is there a way to know that the user has entered a token key to avoid having them to type Connected?"
    (root / ".env").write_text("AIRGRADIENT_TOKEN=\n")
    sleeps = []
    def page_saves_it(seconds):   # the token page saves the token while the command waits
        sleeps.append(seconds)
        if len(sleeps) == 2:
            (root / ".env").write_text(f"AIRGRADIENT_TOKEN={TOKEN}\n")
    monkeypatch.setattr(setup.time, "sleep", page_saves_it)
    code, out = run(root, capsys, "--wait-token", env={})
    assert code == 0 and sleeps == [setup.WAIT_POLL_SECONDS] * 2
    assert out.startswith("Connected to AirGradient: 1 monitor:") and TOKEN not in out


def test_wait_token_gives_up_after_its_limit_and_says_what_to_do_next(root, capsys, monkeypatch):
    (root / ".env").write_text("AIRGRADIENT_TOKEN=\n")
    now = [0.0]
    monkeypatch.setattr(setup.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(setup.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(setup, "account_rows", lambda token, **k: pytest.fail("no token: nothing to look up"))
    code, out = run(root, capsys, "--wait-token", env={})
    assert code == 0 and now[0] >= setup.WAIT_TOKEN_SECONDS
    assert out.startswith("Not saved yet.") and setup.WAIT_TOKEN_CMD in out and "done" in out
    assert setup.WAIT_TOKEN_SECONDS < 120   # one wait fits Claude Code's default 2-minute command limit


def test_setup_md_ends_the_turn_on_the_token_and_waits_after_done():
    # Agents skipped the message when told to send it and wait in the same turn (rehearsals, 23 Nov): the turn ends on it.
    text = (Path(__file__).resolve().parent.parent / "SETUP.md").read_text()
    assert "then end your turn" in text and "When the page says Saved, reply **done** here." in text
    assert "./homeair connect --wait" in text and "no need to reply" not in text


def test_setup_md_says_where_the_link_is_and_how_to_stop_it():
    # review, Important 4, then --start (14 Nov): the start command prints the setup link and restarts the copy it started
    text = doc("SETUP.md")
    assert "run `./homeair connect` and open the address it prints" in text and "?code=" not in text
    assert "`./homeair start` starts or restarts Home Air" in text


def test_status_suggests_starting_in_the_background():
    assert setup.START_CMD == "./homeair start"


def skill_root(root):
    """A Home Air folder with the real skill in it, and an empty home folder beside it."""
    src = REPO / "skills" / setup.SKILL
    (root / "skills" / setup.SKILL / "scripts").mkdir(parents=True)
    for name in ("SKILL.md", "scripts/home_air.py"):
        (root / "skills" / setup.SKILL / name).write_text((src / name).read_text())
    (root / "home").mkdir()
    return root / "home"


def test_agent_on_never_replaces_another_skill_and_off_removes_only_its_own_links(root, capsys, monkeypatch):
    # decision 21 Nov, "Self-install setup connects the agent that ran it": never overwrite, a record, a one-command undo
    home = skill_root(root)
    monkeypatch.setenv("HOME", str(home))   # never the real ~/.claude or ~/.agents
    theirs = home / ".agents" / "skills" / setup.SKILL
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("someone else's skill")
    env = {"HOME": str(home), "CLAUDECODE": "1"}
    code, out = run(root, capsys, "--agent", "on", env=env)
    ours = home / ".claude" / "skills" / setup.SKILL
    assert code == 0 and ours.is_symlink() and ours.resolve() == (root / "skills" / setup.SKILL).resolve()
    assert (theirs / "SKILL.md").read_text() == "someone else's skill" and not theirs.is_symlink()
    assert "already there" in out and setup.AGENT_OFF_CMD in out
    assert run(root, capsys, "--agent", "on", env=env)[0] == 0   # twice is the same as once
    code, out = run(root, capsys, "--agent", "off", env=env)
    assert code == 0 and not ours.exists() and not ours.is_symlink()
    assert (theirs / "SKILL.md").read_text() == "someone else's skill"
    # a link setup made, then replaced by the person's own skill: off leaves theirs
    run(root, capsys, "--agent", "on", env=env)
    ours.unlink()
    ours.mkdir()
    (ours / "SKILL.md").write_text("their own now")
    code, out = run(root, capsys, "--agent", "off", env=env)
    assert code == 0 and (ours / "SKILL.md").read_text() == "their own now" and "no longer the link made during setup" in out


def test_agent_on_with_every_folder_taken_says_it_did_not_connect(root, capsys, monkeypatch):
    home = skill_root(root)
    monkeypatch.setenv("HOME", str(home))
    for folder in (setup.SHARED_SKILLS, setup.CLAUDE_SKILLS):
        (home / folder).mkdir(parents=True)
        (home / folder / setup.SKILL).symlink_to(home)   # a link elsewhere is another skill too
    code, out = run(root, capsys, "--agent", "on", env={"HOME": str(home), "CLAUDECODE": "1"})
    assert code == 1 and "The skill wasn't added" in out and not (root / setup.AGENT_FILE).exists()
    assert run(root, capsys, "--agent", "off", env={"HOME": str(home)})[0] == 0
    assert all((home / f / setup.SKILL).is_symlink() for f in (setup.SHARED_SKILLS, setup.CLAUDE_SKILLS))


def test_agent_on_records_a_link_made_before_a_later_one_fails(root, capsys, monkeypatch):
    # review of agent/lead/mcp-server: the record was written after the loop, so a failure left an unrecorded link
    home = skill_root(root)
    monkeypatch.setenv("HOME", str(home))
    (home / ".claude").mkdir()
    (home / setup.CLAUDE_SKILLS).write_text("not a folder")   # Claude Code's folder can't be made: the second link fails
    code, out = run(root, capsys, "--agent", "on", env={"HOME": str(home), "CLAUDECODE": "1"})
    shared = home / setup.SHARED_SKILLS / setup.SKILL
    assert code == 0 and shared.is_symlink() and "Couldn't link" in out
    assert run(root, capsys, "--agent", "off", env={"HOME": str(home)})[0] == 0 and not shared.is_symlink()


@pytest.mark.parametrize("env, claude_dir, folders", [
    ({"CLAUDECODE": "1"}, False, {setup.SHARED_SKILLS, setup.CLAUDE_SKILLS}),
    ({"CODEX_SANDBOX": "seatbelt"}, True, {setup.SHARED_SKILLS, setup.CLAUDE_SKILLS}),   # Codex runs it, Claude Code installed: both (23 Nov)
    ({"CODEX_SANDBOX": "seatbelt"}, False, {setup.SHARED_SKILLS}),
    ({"CODEX_HOME": "/elsewhere"}, False, {setup.SHARED_SKILLS}),   # a user setting, not Codex running: unknown agent
    ({}, True, {setup.SHARED_SKILLS, setup.CLAUDE_SKILLS}),          # unknown agent, Claude Code installed: both
])
def test_agent_on_links_the_folders_of_the_agent_running_setup(root, capsys, monkeypatch, env, claude_dir, folders):
    home = skill_root(root)
    monkeypatch.setenv("HOME", str(home))
    if claude_dir:
        (home / ".claude").mkdir()
    assert run(root, capsys, "--agent", "on", env={"HOME": str(home), **env})[0] == 0
    assert {f for f in (setup.SHARED_SKILLS, setup.CLAUDE_SKILLS) if (home / f / setup.SKILL).is_symlink()} == folders


def test_agents_found_by_their_folder_or_their_command(tmp_path):
    # setup's last message names the coding agents that got the skill: only ones present on this computer
    home, bin_ = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bin_.mkdir()
    assert setup.agents_found({"PATH": str(bin_)}, home) == []
    (home / ".codex").mkdir()
    (home / ".gemini").write_text("a file, not the agent's folder")
    for name in ("claude", "cursor-agent", "gemini-old"):
        (bin_ / name).write_text("#!/bin/sh\n")
        (bin_ / name).chmod(0o755)
    assert setup.agents_found({"PATH": str(bin_)}, home) == ["Claude Code", "Codex", "Cursor"]
    assert setup.agents_found({}, home) == ["Codex"]   # no PATH: folders only


def test_agent_on_names_the_agents_found_that_read_the_linked_folders(root, capsys, monkeypatch):
    home = skill_root(root)
    monkeypatch.setenv("HOME", str(home))
    for folder in (".codex", ".cursor"):
        (home / folder).mkdir()
    code, out = run(root, capsys, "--agent", "on", env={"HOME": str(home), "CLAUDECODE": "1"})
    assert code == 0 and "Added the Home Air skill to Claude Code, Codex and Cursor on this computer: " in out, out
    assert "Gemini CLI on this computer" not in out and f"To remove the skill: {setup.AGENT_OFF_CMD}" in out
    run(root, capsys, "--agent", "off", env={"HOME": str(home)})
    import shutil
    shutil.rmtree(home / ".claude")   # a computer without Claude Code
    code, out = run(root, capsys, "--agent", "on", env={"HOME": str(home), "CODEX_SANDBOX": "seatbelt"})
    assert code == 0 and "Added the Home Air skill to Codex and Cursor on this computer: " in out, out   # no Claude Code link
    run(root, capsys, "--agent", "off", env={"HOME": str(home)})
    bare = root / "bare"
    bare.mkdir()
    code, out = run(root, capsys, "--agent", "on", env={"HOME": str(bare)})
    assert code == 0 and "Added the Home Air skill to your coding agents on this computer: " in out, out
    run(root, capsys, "--agent", "off", env={"HOME": str(bare)})


def test_stop_stops_the_copy_start_started_by_its_pid_never_the_login_item(root, capsys, monkeypatch):
    import autostart
    stopped = []
    monkeypatch.setattr(autostart, "login_item", lambda root, home: None)
    monkeypatch.setattr(autostart, "stop_setup_copy", lambda root: stopped.append(root) or True)
    (root / setup.PORT_FILE).write_text("5600\n")
    code, out = run(root, capsys, "--stop", env={"HOME": str(root)})
    assert code == 0 and stopped == [root] and "Stopped Home Air (it was at http://localhost:5600)" in out
    monkeypatch.setattr(autostart, "stop_setup_copy", lambda root: False)
    code, out = run(root, capsys, "--stop", env={"HOME": str(root)})
    assert code == 0 and "isn't running from this folder: nothing to stop" in out
    # the login item's copy: the system would start it again, so it is left running and the message says how to turn it off
    monkeypatch.setattr(autostart, "login_item", lambda root, home: ("launchd", 5555))
    monkeypatch.setattr(autostart, "port_answers", lambda port: True)
    monkeypatch.setattr(autostart, "stop_setup_copy", lambda root: pytest.fail("the login item's copy was stopped by its pid"))
    code, out = run(root, capsys, "--stop", env={"HOME": str(root)})
    assert code == 1 and setup.LOGIN_OFF_CMD in out


def test_stop_setup_copy_says_whether_it_stopped_one(tmp_path):
    import autostart
    assert autostart.stop_setup_copy(tmp_path) is False   # no app.pid
    (tmp_path / autostart.PID_FILE).write_text("not a pid")
    assert autostart.stop_setup_copy(tmp_path) is False


# Setup finds the monitors on the Wi-Fi and writes the home, asking nothing. Fake monitors on loopback, found through
# the AIRGRADIENT_LOCAL_FOUND seam: no real network.

IN_SERIAL, OUT_SERIAL = "84fce6111111", "84fce6222222"


@pytest.fixture
def monitors(tmp_path):
    started = []

    def start(*specs):
        """specs: (serial, model, firmware, mode); returns the env that makes discovery find them."""
        found = []
        for serial, model, firmware, mode in specs:
            server = fake.make_local_server(0, serial, model, firmware, mode)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            started.append(server)
            found.append({"serial": serial, "model": model, "firmware": firmware, "address": "127.0.0.1",
                          "port": server.server_address[1]})
        path = tmp_path / "found.json"
        path.write_text(json.dumps(found))
        return {localnet.FOUND_FILE_ENV: str(path)}

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def written(root):
    return tomllib.loads((root / "config.toml").read_text())


def test_a_new_home_starts_with_no_profiles(root, capsys, monitors, calls):
    # An absent [profiles] means every profile (config.py): a new home must say none, so the onboarding asks.
    code, _ = run(root, capsys, env=monitors((IN_SERIAL, "I-9PSL", "3.7.1", None)))
    assert code == 0 and written(root)["profiles"] == {"toddler": False, "sinus": False, "runner": False}
    assert home(root).profiles == {"toddler": False, "sinus": False, "runner": False}


def test_connect_prints_the_token_forms_address(root, capsys):
    """The form's plain address on the port setup chose (no private code since 23 Nov); with a token saved, none."""
    (root / setup.PORT_FILE).write_text("5601\n")
    code, out = run(root, capsys, "--connect", env={})
    assert code == 0 and out == "Add your AirGradient API token at http://localhost:5601/connect\n"
    assert not (root / "connect-code").exists()
    code, out = run(root, capsys, "--connect")   # a token saved
    assert code == 0 and "/connect" not in out and "already saved" in out


def test_setup_writes_both_found_monitors(root, capsys, monitors, calls):
    # Protects what the home is made of: both serials, the unit from the monitor's own /config, and no place guessed.
    env = monitors((IN_SERIAL, "I-9PSL", "3.7.1", None), (OUT_SERIAL, "O-1PST", "3.7.1", None))
    code, out = run(root, capsys, env=env)
    assert code == 0 and out.count("found on your Wi-Fi, read directly") == 2
    cfg = written(root)
    assert cfg["place"]["indoor_serial"] == IN_SERIAL and cfg["place"]["outdoor_serial"] == OUT_SERIAL
    assert cfg["place"]["indoor"] == "home" and cfg["place"]["outdoor"] == "outdoor"
    assert cfg["home"]["temp_unit"] == "F" and "lat" not in cfg["place"] and "sources" not in cfg
    assert cfg["place"]["timezone"]


def test_setup_skips_old_firmware_and_says_why(root, capsys, monitors, calls):
    # An old-firmware monitor has no compensated PM2.5: writing it would make the page read nothing from it.
    env = monitors((IN_SERIAL, "I-9PSL", "3.7.1", None), (OUT_SERIAL, "O-1PST", "3.1.3", "old_firmware"))
    code, out = run(root, capsys, env=env)
    assert code == 0 and "firmware 3.1.3 is older than 3.1.4" in out
    place = written(root)["place"]
    assert place["indoor_serial"] == IN_SERIAL and "outdoor_serial" not in place


def test_setup_reads_the_first_of_a_kind_and_keeps_an_existing_home(root, capsys, monitors, calls):
    # Setup run again must not lose the household's own choices; a second monitor of a kind is named, not written.
    (root / "config.toml").write_text('[place]\nname = "Mine"\nlat = 40.7\nlon = -74.0\ntimezone = "America/New_York"\n'
                                      'indoor = "Bedroom"\n\n[limits]\nco2 = 700\n')
    env = monitors((IN_SERIAL, "I-9PSL", "3.7.1", None), ("84fce6333333", "I-9PSL", "3.7.1", None))
    code, out = run(root, capsys, env=env)
    assert code == 0 and "Also found: another indoor monitor (…3333, I-9PSL)" in out, out
    cfg = written(root)
    assert cfg["place"]["indoor"] == "Bedroom" and cfg["place"]["indoor_serial"] == IN_SERIAL
    assert cfg["place"]["name"] == "Mine" and cfg["limits"]["co2"] == 700
    assert len(list(root.glob("config.toml.bak-*"))) == 1


def test_several_of_a_kind_keep_the_configured_one_else_the_lowest_serial(root, capsys, monitors, calls):
    # Which monitor a home reads must not depend on which answered first, and setup run again must not switch it back.
    third = "84fce6333333"
    env = monitors((third, "I-9PSL", "3.7.1", None), (IN_SERIAL, "I-9PSL", "3.7.1", None))
    code, out = run(root, capsys, env=env)
    assert code == 0 and written(root)["place"]["indoor_serial"] == IN_SERIAL
    assert f"Also found: another indoor monitor (…3333, I-9PSL). To read it instead: {setup.MONITOR_CMD} {third}\n" in out
    path = root / "config.toml"
    path.write_text(path.read_text().replace(IN_SERIAL, third))   # the household switched to the other one
    code, out = run(root, capsys, env=env)
    assert code == 0 and written(root)["place"]["indoor_serial"] == third
    assert f"Also found: another indoor monitor (…1111, I-9PSL). To read it instead: {setup.MONITOR_CMD} {IN_SERIAL}" in out
    env = monitors((third, "I-9PSL", "3.7.1", "wrong_serial"), (IN_SERIAL, "I-9PSL", "3.7.1", None))
    code, out = run(root, capsys, env=env)   # the household's monitor didn't answer this once: it stays, never swapped
    assert code == 0 and written(root)["place"]["indoor_serial"] == third and "didn't answer" in out, out


def test_monitor_flag_reads_a_found_serial_and_drops_the_other_monitors_cloud_id(root, capsys, monitors, calls, monkeypatch):
    # A wrong switch is silent: an unknown serial must change nothing, a cloud id must never stay with another serial
    # (it would show that monitor's cloud row), and with a token a monitor the account doesn't have would be swapped
    # back at the next start, so it is refused up front.
    third = "84fce6333333"
    env = monitors((IN_SERIAL, "I-9PSL", "3.7.1", None), (third, "I-9PSL", "3.7.1", None), (OUT_SERIAL, "O-1PST", "3.7.1", None))
    path = root / "config.toml"
    path.write_text(f'[place]\ntimezone = "America/New_York"\nindoor = "Bedroom"\nindoor_id = 7001\nindoor_serial = "{IN_SERIAL}"\n'
                    f'outdoor = "Porch"\noutdoor_id = 7002\noutdoor_serial = "{OUT_SERIAL}"\n')
    before = path.read_text()
    code, out = run(root, capsys, "--monitor", "84fce6999999", env=env)
    assert code == 1 and calls == [] and path.read_text() == before and not list(root.glob("config.toml.bak-*"))
    assert out.count("\n") == 1 and all(s in out for s in ("84fce6999999", IN_SERIAL, third, OUT_SERIAL)), out
    monkeypatch.setattr(setup, "account_rows", lambda token, **k: [{**INDOOR, "serialno": IN_SERIAL.upper()},
                                                                   {**OUTDOOR, "serialno": OUT_SERIAL}])
    code, out = run(root, capsys, "--monitor", third, env={**env, "AIRGRADIENT_TOKEN": TOKEN})
    assert code == 1 and calls == [] and path.read_text() == before and "isn't in your AirGradient account" in out, out
    code, out = run(root, capsys, "--monitor", third.upper(), env=env)
    assert code == 0 and calls == ["start_app"], out
    place = written(root)["place"]
    assert place["indoor_serial"] == third and "indoor_id" not in place and place["indoor"] == "Bedroom"
    assert place["outdoor_serial"] == OUT_SERIAL and place["outdoor_id"] == 7002
    assert len(list(root.glob("config.toml.bak-*"))) == 1
    code, out = run(root, capsys, "--monitor", third, env=env)   # already read: nothing to change
    assert code == 0 and calls == ["start_app"] and len(list(root.glob("config.toml.bak-*"))) == 1


def test_monitors_lists_each_one_found_and_which_ones_are_read(root, capsys, monitors, calls):
    third = "84fce6333333"
    env = monitors((IN_SERIAL, "I-9PSL", "3.7.1", None), (third, "I-9PSL", "3.7.1", None),
                   (OUT_SERIAL, "O-1PST", "3.1.3", "old_firmware"))
    path = root / "config.toml"
    path.write_text(f'[place]\ntimezone = "America/New_York"\nindoor = "Bedroom"\nindoor_serial = "{IN_SERIAL}"\n'
                    f'outdoor = "Porch"\noutdoor_serial = "84fce6444444"\n')
    before = path.read_text()
    code, out = run(root, capsys, "--monitors", env=env)
    assert code == 0 and path.read_text() == before and calls == [], out
    assert out.splitlines() == [
        f"indoor {IN_SERIAL} (I-9PSL): read by Home Air",
        f"outdoor {OUT_SERIAL} (O-1PST): not read: firmware 3.1.3 is older than 3.1.4: update it in the AirGradient app",
        f"indoor {third} (I-9PSL): not read. To read it instead: {setup.MONITOR_CMD} {third}",
        "outdoor 84fce6444444: read by Home Air, but it didn't answer on the Wi-Fi now"]
    code, out = run(root, capsys, "--monitors", env=monitors())
    assert code == 0 and out.startswith("No AirGradient monitor answered on this network.")


def test_nothing_found_exits_0_with_the_line(root, capsys, monitors, monkeypatch, tmp_path, calls):
    # Nothing on the Wi-Fi is an ordinary home (the token path), never an error and never a hang.
    env = monitors()
    start = time.monotonic()
    code, out = run(root, capsys, env=env)
    assert code == 0 and time.monotonic() - start < 5
    assert "No AirGradient monitor answered on this network" in out and "Nothing is read yet" in out
    cfg = written(root)
    assert "indoor" not in cfg["place"] and "outdoor" not in cfg["place"]
    # a home without a place or monitor loads (the server starts with it), and nobody is picked yet: "Who lives here" is unset
    home_cfg = load_config({}, root)
    assert home_cfg.place is not None and home_cfg.place.lat is None and not config_monitors(home_cfg)
    assert home_cfg.profiles == {"toddler": False, "sinus": False, "runner": False}
    proc = tmp_path / "version"
    proc.write_text("Linux version 5.15 microsoft-standard-WSL2")
    real = localnet.environment_blocker
    monkeypatch.setattr(localnet, "environment_blocker", lambda env=os.environ, proc_version=None: real(env, proc))
    (root / "config.toml").unlink()
    code, out = run(root, capsys, env={})
    assert code == 0 and "WSL can't find the monitors on your Wi-Fi" in out


def test_nothing_found_in_a_container_says_where_the_token_goes(root, capsys, monkeypatch, calls):
    monkeypatch.setattr(localnet, "environment_blocker", lambda *a, **k: "container")
    code, out = run(root, capsys, env={})
    assert code == 0 and "a container can't find the monitors on your Wi-Fi" in out and setup.HOME_SERVER_CMD in out



def test_bare_setup_runs_each_step_once_in_order_and_ends_with_the_live_check(root, capsys, monitors, calls):
    # One command is the whole setup: a step missing or out of order leaves no running page or no skill, silently.
    code, _ = run(root, capsys, env=monitors((IN_SERIAL, "I-9PSL", "3.7.1", None)))
    assert code == 0 and calls == ["start_app", "start_at_login", "open_page", "live"]   # the skill is offered after, not added


def test_bare_setup_with_nothing_found_opens_the_page_and_skips_the_live_check(root, capsys, monitors, calls):
    # No monitor is an ordinary home: the page opens on the token card, and "Not live yet" would read as a failure.
    code, out = run(root, capsys, env=monitors())
    assert code == 0 and calls == ["start_app", "start_at_login", "open_page"]
    assert "Nothing is read yet" in out and "--wait" in out


def test_bare_setup_restarts_the_app_when_the_login_item_cannot_start(root, capsys, monitors, calls, monkeypatch):
    # Turning the login item on stops the copy --start made; when it can't start (busy port, no systemd) the page must still answer.
    monkeypatch.setattr(setup, "start_at_login", lambda *a, **k: calls.append("start_at_login") or 1)
    monkeypatch.setattr(setup.autostart if hasattr(setup, "autostart") else __import__("autostart"), "port_answers", lambda port: False)
    code, _ = run(root, capsys, env=monitors((IN_SERIAL, "I-9PSL", "3.7.1", None)))
    assert code == 0 and calls == ["start_app", "start_at_login", "start_app", "open_page", "live"]


def test_live_waits_for_the_first_local_reads_of_an_app_just_started(root, monkeypatch, capsys):
    # The one-command setup checks the page seconds after starting it: a monitor not read yet says "not answering",
    # and the run would end "Not live yet" on a home that is fine a moment later (seen on the real Wi-Fi, 22 Nov).
    states = iter([{"indoor": "stale"}, {"indoor": "stale"}, {"indoor": "local"}])
    class Reply:
        text = ""
        def __init__(self, value):
            self.value = value
        def json(self):
            return self.value
    def get(url):
        return Reply({"status": "ok", "airgradient_token_set": False, "monitors": next(states)})
    monkeypatch.setattr(setup.time, "sleep", lambda s: None)
    code = setup.live("http://x:1", {}, root, get=get, wait=10)
    out = capsys.readouterr().out
    assert "indoor: on your Wi-Fi" in out and "not answering" not in out   # (the stub page has no settings, so code is not asserted)


def test_start_at_login_waits_for_the_first_wifi_reading_and_says_to_press_allow(capsys):
    # On a Mac the login item's copy reads nothing until the user answers macOS's "uv" prompt (22 Nov, real home): setup
    # must wait for it, say what to press while it waits, and say how to fix it when it never comes, never end silently.
    class Reply:
        def __init__(self, value):
            self.value = value
        def json(self):
            return self.value
    now = [0.0]
    states = iter([{"indoor": "stale", "outdoor": "stale"}] * 4 + [{"indoor": "local", "outdoor": "local"}])
    sleep = lambda s: now.__setitem__(0, now[0] + s)
    assert setup.wait_wifi_reads("http://localhost:1", ["indoor", "outdoor"], get=lambda u: Reply({"monitors": next(states)}),
                                 clock=lambda: now[0], sleep=sleep)
    out = capsys.readouterr().out
    assert out.count('press Allow on macOS\'s prompt "Allow "uv"') == 1 and "Indoor, outdoor: read on your Wi-Fi" in out
    now[0] = 0.0
    assert not setup.wait_wifi_reads("http://localhost:1", ["indoor"], get=lambda u: Reply({"monitors": {"indoor": "stale"}}),
                                     clock=lambda: now[0], sleep=sleep)
    out = capsys.readouterr().out
    assert now[0] <= setup.LOGIN_READ_WAIT + setup.WAIT_POLL_SECONDS and setup.LOCAL_NETWORK in out and '"uv"' in out


def test_start_at_login_fails_when_no_wifi_reading_comes(root, monkeypatch):
    # The wait's verdict must reach the exit code: an agent running --start-at-login on reads success otherwise (Codex
    # review, 22 Nov). one_run still goes on while the page answers.
    import autostart
    monkeypatch.setattr(autostart, "turn_on", lambda *a, **k: 0)
    monkeypatch.setattr(setup.sys, "platform", "darwin")
    monkeypatch.setattr(setup, "known_serials", lambda r: True)
    monkeypatch.setattr(setup, "wait_wifi_reads", lambda url, kinds: False)
    assert setup.start_at_login("on", {}, root) != 0
    monkeypatch.setattr(setup, "wait_wifi_reads", lambda url, kinds: True)
    assert setup.start_at_login("on", {}, root) == 0


def test_live_names_macos_local_network_permission_when_the_app_cannot_reach_monitors_this_computer_sees(root, capsys, monitors):
    # A login item macOS hasn't allowed reads nothing ("No route to host") while the OS still finds the monitors by name
    # (22 Nov, real home): --live must name that cause and its one fix, not "is it plugged in?", and only on a Mac, for
    # a page on this computer.
    setup.find_home(monitors((IN_SERIAL, "I-9PSL", "3.7.1", None)), root)
    class Reply:
        text = ""
        def __init__(self, value):
            self.value = value
        def json(self):
            return self.value
    health = {"status": "ok", "airgradient_token_set": False, "monitors": {"indoor": "stale"}}
    get = lambda url: Reply(health)
    asked = []
    reach = lambda serial: asked.append(serial) or "no_route"
    code = setup.live("http://localhost:5574", {"HOME": str(root)}, root, get=get, reach=reach, platform="darwin")
    out = capsys.readouterr().out
    assert code == 1 and asked == [IN_SERIAL] and setup.LOCAL_NETWORK in out and "plugged in" not in out, out
    assert "turn it on for the app Home Air was started from" in out   # no login item in this folder
    import autostart
    autostart.plist_path(root).parent.mkdir(parents=True)
    autostart.plist_path(root).write_text(autostart.launchd_plist(root, root, 5574))
    setup.live("http://localhost:5574", {"HOME": str(root)}, root, get=get, reach=reach, platform="darwin")
    assert 'turn it on for "uv"' in capsys.readouterr().out   # the login item's copy runs through uv
    for kwargs in ({"platform": "linux"}, {"platform": "darwin", "url": "http://destination:8000"}):
        url = kwargs.pop("url", "http://localhost:5574")
        setup.live(url, {"HOME": str(root)}, root, get=get, reach=reach, **kwargs)
        out = capsys.readouterr().out
        assert "plugged in" in out and setup.LOCAL_NETWORK not in out, out


def test_a_connection_that_ends_in_no_route_to_host_is_told_apart(monkeypatch):
    # The --live diagnosis rests on this one signal: requests wraps the socket's EHOSTUNREACH three levels deep.
    import errno
    import urllib3
    cause = OSError(errno.EHOSTUNREACH, "No route to host")
    inner = urllib3.exceptions.NewConnectionError(None, "Failed to establish a new connection")
    inner.__cause__ = cause
    wrapped = requests.ConnectionError(urllib3.exceptions.MaxRetryError(None, "/measures/current", reason=inner))
    def get(url, **kw):
        raise wrapped
    with pytest.raises(localnet.LocalReadError, match=localnet.NO_ROUTE):
        localnet.read_current(None, "192.168.1.20", 80, get=get)
    def refused(url, **kw):
        raise requests.ConnectionError("Connection refused")
    with pytest.raises(localnet.LocalReadError, match="ConnectionError"):
        localnet.read_current(None, "192.168.1.20", 80, get=refused)


def test_start_at_login_counts_a_home_with_nothing_to_read_as_answering(root, monkeypatch):
    # No monitor found and no token (rehearse S9, 23 Nov): the page answers but --live can't pass, and start at login
    # said "the page didn't answer" and skipped writing app.port. Nothing to read yet: the page answering is enough.
    class Ok:
        ok = True
    monkeypatch.setattr(setup.requests, "get", lambda *a, **k: Ok())
    monkeypatch.setattr(setup, "known_serials", lambda r: [])
    monkeypatch.setattr(setup, "has_saved_token", lambda env, r: False)
    monkeypatch.setattr(setup, "live", lambda *a, **k: 1)
    assert setup.page_answers("http://localhost:1", {}, root) == 0


def test_start_at_login_uses_the_port_setup_chose(root, monkeypatch):
    # Sacha's Mac (23 Nov): 5555 was taken, setup started Home Air on 5601, and start at login tried 5555 and failed.
    import autostart
    seen = {}
    monkeypatch.setattr(autostart, "turn_on", lambda root_, home, env, *a: seen.update(port=env.get("PORT")) or 1)
    (root / setup.PORT_FILE).write_text("5601\n")
    setup.start_at_login("on", {"HOME": str(root)}, root, quiet=True)
    assert seen["port"] == "5601"
    setup.start_at_login("on", {"HOME": str(root), "PORT": "5700"}, root, quiet=True)
    assert seen["port"] == "5700"   # a PORT the person set still wins


def test_the_skill_goes_to_claude_code_too_when_another_agent_runs_setup(tmp_path):
    # Codex ran setup on a Mac with Claude Code (23 Nov): only ~/.agents/skills was linked, though the question named both.
    (tmp_path / ".claude").mkdir()
    agent, targets = setup.agent_targets({"CODEX_THREAD_ID": "t"}, tmp_path)
    assert tmp_path / ".claude" / "skills" / setup.SKILL in targets and tmp_path / ".agents" / "skills" / setup.SKILL in targets


def test_a_file_error_names_the_file(root, capsys, monkeypatch):
    # Codex's sandbox (rehearse S12, 24 Nov): "Couldn't read or write a setup file" didn't say which one.
    monkeypatch.setattr(setup, "one_run", lambda env, root: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted", "/x/Library/LaunchAgents/h.plist")))
    code, out = run(root, capsys, env={})
    assert code == 1 and "/x/Library/LaunchAgents/h.plist" in out and "Operation not permitted" in out


def test_skill_status_names_the_agents_found(root, capsys, monkeypatch):
    # SETUP.md's skill step names the agents before asking (mock 111): status is where it reads them, without linking.
    home = skill_root(root)
    (home / ".codex").mkdir()
    (home / ".claude").mkdir()
    code, out = run(root, capsys, "--agent", "status", env={"HOME": str(home), "PATH": ""})
    assert code == 0 and "Coding agents on this computer: Claude Code and Codex" in out
    assert not (home / setup.SHARED_SKILLS / setup.SKILL).exists()


def test_a_monitor_that_misses_one_read_is_still_read(root, monkeypatch, monitors):
    # Sacha's Mac (24 Nov): other programs read the same monitors, the outdoor one missed setup's single read, and
    # setup left it out of config.toml. A few tries, a second apart, before saying it didn't answer.
    env = monitors((IN_SERIAL, "I-9PSL", "3.7.1", None), (OUT_SERIAL, "O-1PST", "3.7.1", None))
    found = localnet.discover(env=env)
    misses = {OUT_SERIAL: 1}
    def flaky(_session, address, port):
        item = next(f for f in found if f.port == port)
        if misses.get(item.serial):
            misses[item.serial] -= 1
            raise OSError("busy")
        return localnet.read_current(None, address, port)
    monkeypatch.setattr(setup, "READ_RETRY_WAIT", 0)
    chosen, lines = setup.found_monitors(found, reader=flaky)
    assert set(chosen) == {"indoor", "outdoor"} and not any("didn't answer" in l for l in lines)


def test_setup_writes_keys_once_without_printing(root, capsys, calls):
    from config import parse_dotenv
    run(root, capsys, env={})
    first = parse_dotenv((root / ".env").read_text())
    assert first["VAPID_PRIVATE_KEY"] and first["VAPID_PUBLIC_KEY"]
    code, out = run(root, capsys, env={})
    assert parse_dotenv((root / ".env").read_text()) == first
    assert "Alert keys ready" not in out
    assert first["VAPID_PRIVATE_KEY"] not in out and first["VAPID_PUBLIC_KEY"] not in out
    root2 = root / "second"; root2.mkdir()
    code, out = run(root2, capsys, env={})
    assert "Alert keys ready" in out and parse_dotenv((root2 / ".env").read_text())["VAPID_PRIVATE_KEY"] not in out


def test_railway_sends_nothing_when_a_road_arc_stays_in_degrees(root, monkeypatch, capsys):
    (root / "config.toml").write_text('[place]\nname = "Ogden"\ntimezone = "America/Denver"\n\n[[roads]]\nname = "x"\nkey = "x"\n'
                                      'toward = [\n  74,  # a comment inside the array\n  162,\n]\n')
    (root / ".env").write_text(f"AIRGRADIENT_TOKEN={TOKEN}\n")
    sent = []
    monkeypatch.setattr(setup.subprocess, "run", lambda args, **k: sent.append(args))
    code, out = run(root, capsys, "railway", "--service", "home-air")
    assert code != 0 and sent == [] and "toward = [74, 162]" in out


# ---- the roads ---------------------------------------------------------------------------------------------

ROADS_FIXTURES = REPO / "tests" / "fixtures" / "roads"
ROADS_CLIP = (ROADS_FIXTURES / "nyc-2026-11-21-clip.json.gz").read_bytes()
ROADS_BASE = "https://data.example/roads"
ROADS_NAME = "nyc-2026-11.json.gz"


@pytest.mark.parametrize("lat,lon,want", [
    (40.7, -74, "nyc"), (41.2, -74, "nyc-outer"),
    (40.5, -75.5, "block"), (61, -149, "ak"), (21, -157, "hi"),
    (45, -110, None), (44, -112, None),
    (46, -110, "not_built"), (45, -108, "not_built"), (51.5, -0.12, "not_built"),
])
def test_setup_us_metros_blocks_and_empty_coverage(root, served_roads, monkeypatch, capsys, lat, lon, want):
    import gzip
    entries = [
        {"id": "block", "kind": "block", "box": [-76, 40, -72, 42], "level": 2},
        {"id": "nyc-outer", "kind": "metro", "box": [-75, 40, -73, 42], "level": "counted"},
        {"id": "nyc", "box": [-74.4, 40.4, -73.6, 41], "level": 1},
        {"id": "ak", "kind": "block", "box": [-150, 60, -148, 62], "level": "counted"},
        {"id": "hi", "kind": "block", "box": [-158, 20, -156, 22], "level": "counted"},
    ]
    for e in entries:
        doc = {"country": "US", "roads": {"main": {"name": e["id"]}},
               "features": [{"type": "Feature", "properties": {"g": "main", "c": "motorway"},
                             "geometry": {"type": "LineString", "coordinates": [
                                 [lon + .001, lat - .002], [lon + .001, lat + .002]]}}]}
        data = gzip.compress(json.dumps(doc).encode(), mtime=0)
        e.update(country="US", file=e["id"] + "-2026-11.json.gz", bytes=len(data),
                 sha256=hashlib.sha256(data).hexdigest())
        served_roads[e["file"]] = data
    served_roads["index.json"] = json.dumps({
        "metros": entries, "coverage": {"US": [[-112, 44, -110, 46], [-110, 44, -108, 46]]},
    }).encode()
    calls = []
    before = setup.requests.get

    def get(url, **kwargs):
        if url.startswith(ROADS_BASE + "/"):
            calls.append(url.removeprefix(ROADS_BASE + "/"))
        return before(url, **kwargs)

    monkeypatch.setattr(setup.requests, "get", get)
    assert run_setup(root, "--lat", str(lat), "--lon", str(lon)) == 0
    output = capsys.readouterr().out
    saved = tomllib.loads((root / "config.toml").read_text()).get("roads", [])
    if want in (None, "not_built"):
        assert saved == [] and calls == ["index.json"]
        assert ("isn't built for your area yet" in output) == (want == "not_built")
        assert (root / "road-data" / "outcome").read_text() == ("not_built" if want else "none")
    else:
        assert [(r["name"], r["country"], r.get("slow", [])) for r in saved] == [(want, "US", [])]
        name = want + "-2026-11.json.gz"
        assert calls == ["index.json", name]
        assert (root / "road-data" / name).read_bytes() == served_roads[name]
        before_config = (root / "config.toml").read_bytes()
        assert run_setup(root, "--lat", str(lat), "--lon", str(lon)) == 0
        assert (root / "config.toml").read_bytes() == before_config
        assert calls == ["index.json", name]


@pytest.fixture
def served_roads(root, monkeypatch):
    """The data host: index.json and the NYC clip. Everything else keeps the root fixture's refusals."""
    index = {"version": 1, "metros": [{"id": "nyc", "name": "New York", "level": 1, "country": "US",
                                       "box": [-74.359, 40.479, -73.671, 40.960], "file": ROADS_NAME,
                                       "bytes": len(ROADS_CLIP), "sha256": hashlib.sha256(ROADS_CLIP).hexdigest()}]}
    files = {"index.json": json.dumps(index).encode(), ROADS_NAME: ROADS_CLIP}
    before = setup.requests.get
    monkeypatch.setattr(road_data, "ROADS_BASE", ROADS_BASE)
    monkeypatch.delenv("ROAD_DATA_DIR", raising=False)

    class Reply:
        def __init__(self, body):
            self.status_code, self.body = (200 if body is not None else 404), body or b""
        def close(self):
            pass
        def iter_content(self, n):
            return (self.body[i:i + n] for i in range(0, len(self.body), n))

    def get(url, **k):
        if url.startswith(ROADS_BASE + "/"):
            return Reply(files.get(url.removeprefix(ROADS_BASE + "/")))
        return before(url, **k)
    monkeypatch.setattr(setup.requests, "get", get)
    return files


def run_setup(root, *args):
    return setup.main(list(args), {"AIRGRADIENT_TOKEN": TOKEN}, root)


@pytest.mark.parametrize("lat,lon,expect", [
    (40.7127, -73.9590, [("the BQE", "within 500 m", "E")]),
    (40.6720, -73.9745, []),   # Park Slope: none
    (39.95, -75.16, None),     # Philadelphia: not built
])
def test_setup_writes_the_roads_without_a_question(root, served_roads, capsys, lat, lon, expect):
    assert run_setup(root, "--lat", str(lat), "--lon", str(lon)) == 0
    roads = tomllib.loads((root / "config.toml").read_text()).get("roads", [])
    assert [(r["name"], r["band"], r["side"]) for r in roads if r["on"]] == (expect or [])
    assert all(r["found"] == "auto" for r in roads)
    out = capsys.readouterr().out
    assert "?" not in out.split("Roads:")[1].splitlines()[0]
    assert ("isn't built for your area yet" in out) == (expect is None)


def test_the_home_command_finds_the_roads_and_leaves_a_hand_set_road_alone(root, served_roads, monkeypatch, capsys):
    road = f'[road]\nname = "my street"  # by hand\nroute = "{REPO / "static" / "bqe.json"}"\ntoward = [80, 200]\n'
    (root / "config.toml").write_text(f'[place]\ntimezone = "America/Denver"\nindoor = "home"\nindoor_serial = "{IN_SERIAL}"\n\n{road}')
    code, out = run(root, capsys, "--lat", "40.7127", "--lon", "-73.959", env={})
    assert code == 0 and "Roads: the BQE (highway, within 500 m, to the east)" in out, out
    text = (root / "config.toml").read_text()
    assert road in text and any(r["name"] == "the BQE" for r in tomllib.loads(text)["roads"])


def test_a_road_download_that_fails_changes_neither_the_exit_code_nor_the_file(root, capsys):
    code, out = run(root, capsys, "--lat", "40.7127", "--lon", "-73.959")   # no data host: the root fixture refuses it
    assert code == 0, out
    assert "Roads: couldn't get the road data; Home Air on this computer tries again at its next start, at most once a day." in out
    assert "roads" not in tomllib.loads((root / "config.toml").read_text())


def test_railway_sends_sectors_not_degrees(root, monkeypatch, served_roads):
    assert run_setup(root, "--lat", "40.7127", "--lon", "-73.959") == 0
    (root / ".env").write_text(f"AIRGRADIENT_TOKEN={TOKEN}\n")
    sent = {}
    monkeypatch.setattr(setup.subprocess, "run", lambda args, **k: sent.update({args[3]: k["input"]}))
    assert setup.main(["railway", "--service", "home-air"], {}, root) == 0
    roads = tomllib.loads(sent["CONFIG_TOML"])["roads"]
    assert roads and all(isinstance(s, str) for r in roads for k in ("toward", "toward_calm") for s in r.get(k, []))
    assert not re.search(r"\d+\.\d{3,}", sent["CONFIG_TOML"])


def test_a_token_backed_address_updates_config_in_place(root, served_roads, capsys):
    # a saved token takes the account path: it must keep the hand-set [road], hand-written [[roads]], limits, profiles,
    # home settings and comments byte for byte, and change only the place, the monitors and the auto roads
    keep = (f'[road]\nname = "my street"  # by hand\nroute = "{REPO / "static" / "bqe.json"}"\ntoward = [80, 200]\n\n'
            '[[roads]]\nname = "Kent Avenue"  # the truck route\nkind = "main road"\nside = "W"\n\n'
            '[limits]\nco2 = 900  # stricter\n\n[profiles]\ntoddler = true\nsinus = false\nrunner = false\n\n'
            '[home]\ntemp_unit = "C"  # my choice\n')
    (root / "config.toml").write_text('# my home\n[place]\nname = "Old"\nlat = 39.74\nlon = -104.98\n'
                                      f'timezone = "America/Denver"\n\n{keep}')
    code, out = run(root, capsys, "--lat", "40.7127", "--lon", "-73.959")
    assert code == 0, out
    text = (root / "config.toml").read_text()
    assert text.startswith("# my home\n[place]")
    for block in (keep.split("[[roads]]")[0], '[[roads]]\nname = "Kent Avenue"  # the truck route\nkind = "main road"\nside = "W"\n',
                  '[limits]\nco2 = 900  # stricter\n\n[profiles]\ntoddler = true\nsinus = false\nrunner = false\n\n[home]\ntemp_unit = "C"  # my choice\n'):
        assert block in text
    data = tomllib.loads(text)
    assert data["place"]["lat"] == 40.7127 and data["profiles"]["toddler"] is True and data["limits"]["co2"] == 900
    assert data["road"]["name"] == "my street" and any(r["name"] == "the BQE" for r in data["roads"])


def test_widened_public_box_does_not_change_the_polar_pick(monkeypatch):
    place = setup.Place(89.9, 0, 'Polar test', 'test', 'test')
    monitors = [
        {'locationId': 9001, 'latitude': 89.9, 'longitude': 15, 'measuredAt': NOW},  # 2.90 km, outside old box
        {'locationId': 9002, 'latitude': 89.926, 'longitude': 0, 'measuredAt': NOW},  # 2.89 km, old pick
    ]

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{**m, 'dataSource': 'AirGradient', 'pm25': 4} for m in monitors]

    monkeypatch.setattr(setup.publicmap.upstream, 'get', lambda *a, **kw: Resp())
    # Move the newly admitted candidate closer than the original while keeping it outside the old box.
    monitors[0]['longitude'] = 14.6  # 2.83 km; old edge 14.373°
    assert setup.outdoor_source(place) == 9002


@pytest.mark.parametrize('lat,lon', [(0, 180.00125), (89.9, 0), (-89.9, -179.999)])
def test_public_area_box_edges_are_valid(monkeypatch, lat, lon):
    seen = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def get(*args, **kwargs):
        seen.append(kwargs['params'])
        return Resp()

    monkeypatch.setattr(setup.publicmap.upstream, 'get', get)
    setup.publicmap.fetch_area(lat, lon, 17)
    assert len(seen) == 1
    p = seen[0]
    assert -180 <= p['xmin'] <= p['xmax'] <= 180
    assert -90 <= p['ymin'] <= p['ymax'] <= 90
