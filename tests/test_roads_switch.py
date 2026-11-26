"""The road switches on the page: POST /api/roads/switch writes one [[roads]] entry's `on` (config.toml, a backup first)
on the computer Home Air runs on, and is refused elsewhere. Made-up config, no network."""
import difflib
import os

import pytest

import server
from config import backups, load_config

BASE = "http://localhost:5555"
SAME_ORIGIN = {"Origin": BASE}
OTHER_SITE = {"Origin": "https://example.org"}
PLACE = '[place]\nname = "Williamsburg"\nlat = 40.71\nlon = -73.96\ntimezone = "America/New_York"\n'
ROADS = '''
[[roads]]
name = "the BQE"
kind = "highway"
key = "bqe"
band = "within 500 m"
side = "E"
toward = ["E", "SE"]
toward_calm = ["E", "SE"]
found = "auto"
on = true

# a hand-written block, in single quotes
[[roads]]
name = "4th Avenue"
kind = "road"
key = '4th-avenue'
band = "within 500 m"
side = "W"
toward = ["W"]
toward_calm = ["W"]
found = "auto"
on = true
'''


ROAD_TEMPLATE = '''
[[roads]]
name = "Road {i}"
kind = "road"
key = "r{i}"
band = "within 500 m"
side = "N"
toward = ["N"]
toward_calm = ["N"]
found = "auto"
on = true
'''


def changed_lines(before, after):
    return [l[2:] for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l.startswith("+ ")]


@pytest.fixture
def cfg_path(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(PLACE + ROADS, encoding="utf-8")
    monkeypatch.delenv("CONFIG_TOML", raising=False)
    monkeypatch.setenv("CONFIG_FILE", str(path))
    for marker in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "VERCEL", "RENDER", "FLY_APP_NAME", "K_SERVICE", "DYNO",
                   "KUBERNETES_SERVICE_HOST", "KOYEB_APP_NAME", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setattr(server.token_setup, "DOCKER_MARKER", tmp_path / "no-container")
    monkeypatch.setattr(server.token_setup, "PODMAN_MARKER", tmp_path / "no-podman")
    monkeypatch.setattr(server, "token_state", server.token_setup.TokenState())
    old = server.CFG
    server.use_config(load_config(os.environ))
    yield path
    server.use_config(old)


class Client:
    def __init__(self, c, env=None):
        self.c, self.env = c, env or {}

    def post(self, url, **kw):
        with pytest.MonkeyPatch.context() as mp:   # a hosted copy's markers show only during its own requests
            for name, value in self.env.items():
                mp.setenv(name, value)
            return self.c.post(url, base_url=BASE, **kw)


@pytest.fixture
def local_client(cfg_path):
    server.app.config["TESTING"] = True
    yield Client(server.app.test_client())


@pytest.fixture
def hosted_client(cfg_path):
    server.app.config["TESTING"] = True
    yield Client(server.app.test_client(), {"RAILWAY_ENVIRONMENT": "production"})


def test_switch_writes_one_entry_with_a_backup(local_client, cfg_path):   # Done when 5
    before = cfg_path.read_text()
    r = local_client.post("/api/roads/switch", json={"key": "4th-avenue", "on": False}, headers=SAME_ORIGIN)
    assert r.status_code == 200 and "4th Avenue" not in [x["name"] for x in r.get_json()["roads"] if x["on"]]
    assert changed_lines(before, cfg_path.read_text()) == ["on = false"] and backups(cfg_path)
    assert [x["on"] for x in server.APP_VIEW["roads"]] == [True, False]   # the page shows it at once


def test_switch_on_again_and_missing_on_line(local_client, cfg_path):
    cfg_path.write_text(cfg_path.read_text().replace("on = true\n", "", 1), encoding="utf-8")   # the BQE has no `on`
    r = local_client.post("/api/roads/switch", json={"key": "bqe", "on": False}, headers=SAME_ORIGIN)
    assert r.status_code == 200 and r.get_json()["roads"][0]["on"] is False
    r = local_client.post("/api/roads/switch", json={"key": "bqe", "on": True}, headers=SAME_ORIGIN)
    assert r.status_code == 200 and r.get_json()["roads"][0]["on"] is True


def test_switch_refused_online_cross_site_or_stale(local_client, hosted_client, cfg_path):   # Done when 5, Review Focus 4
    assert hosted_client.post("/api/roads/switch", json={"key": "bqe", "on": False}, headers=SAME_ORIGIN).status_code == 403
    assert local_client.post("/api/roads/switch", json={"key": "bqe", "on": False}, headers=OTHER_SITE).status_code == 403
    assert local_client.post("/api/roads/switch", json={"key": "bqe", "on": False}).status_code == 403
    assert local_client.post("/api/roads/switch", data="key=bqe", headers=SAME_ORIGIN).status_code == 403
    before = cfg_path.read_bytes()
    assert local_client.post("/api/roads/switch", json={"key": "gone", "on": True}, headers=SAME_ORIGIN).status_code == 409
    assert local_client.post("/api/roads/switch", json={"key": "bqe", "on": "no"}, headers=SAME_ORIGIN).status_code == 400
    assert cfg_path.read_bytes() == before and not backups(cfg_path)


def test_unchanged_switch_keeps_the_file_as_source(local_client, cfg_path):
    r = local_client.post("/api/roads/switch", json={"key": "bqe", "on": True}, headers=SAME_ORIGIN)   # already on
    assert r.status_code == 200 and server.CFG.source == "CONFIG_FILE" and not backups(cfg_path)
    assert local_client.post("/api/roads/switch", json={"key": "bqe", "on": False}, headers=SAME_ORIGIN).status_code == 200


def test_fifth_followed_road_refused(local_client, cfg_path):
    extra = "".join(ROAD_TEMPLATE.format(i=i) for i in range(3, 7))   # six roads: BQE, 4th Avenue and four more
    cfg_path.write_text(cfg_path.read_text() + extra.replace("on = true", "on = false"), encoding="utf-8")
    server.use_config(load_config(os.environ))
    for i in (3, 4):
        assert local_client.post("/api/roads/switch", json={"key": f"r{i}", "on": True}, headers=SAME_ORIGIN).status_code == 200
    before, n = cfg_path.read_bytes(), len(backups(cfg_path))
    r = local_client.post("/api/roads/switch", json={"key": "r5", "on": True}, headers=SAME_ORIGIN)
    assert r.status_code == 409 and "at most 4 roads" in r.get_json()["error"]
    assert cfg_path.read_bytes() == before and len(backups(cfg_path)) == n


def test_switch_refused_for_proxy_remote_container_and_mixed_origins(local_client, cfg_path, tmp_path, monkeypatch):
    body = {"key": "bqe", "on": False}
    before = cfg_path.read_bytes()
    assert local_client.post("/api/roads/switch", json=body, headers={**SAME_ORIGIN, "X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert local_client.post("/api/roads/switch", json=body, headers=SAME_ORIGIN, environ_overrides={"REMOTE_ADDR": "192.168.1.5"}).status_code == 403
    assert local_client.post("/api/roads/switch", json=body, headers={"Origin": BASE, "Referer": "https://example.org/"}).status_code == 403
    marker = tmp_path / "container"
    marker.write_text("")
    monkeypatch.setattr(server.token_setup, "DOCKER_MARKER", marker)
    assert local_client.post("/api/roads/switch", json=body, headers=SAME_ORIGIN).status_code == 403
    assert cfg_path.read_bytes() == before and not backups(cfg_path)


def test_switch_refused_when_config_is_a_variable(local_client, cfg_path, monkeypatch):
    monkeypatch.setenv("CONFIG_TOML", cfg_path.read_text())
    server.use_config(load_config(os.environ))
    r = local_client.post("/api/roads/switch", json={"key": "bqe", "on": False}, headers=SAME_ORIGIN)
    assert r.status_code == 403 and "Turn off the BQE" in r.get_json()["error"]
