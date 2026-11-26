"""The local read: the mapper to the cloud row's shape, the read's limits, discovery and the poller."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import localnet

SERIAL = "84fce612345a"
NOW = datetime(2026, 11, 22, 15, 0, 0, tzinfo=timezone.utc)
INDOOR = localnet.Monitor("indoor", SERIAL, "Living room", localnet.LOCAL_IDS["indoor"], None)

# Keys of src/AgValue.cpp, as the monitor sends them (22 Nov indoor reply, made-up values).
PAYLOAD = {"pm01": 3.1, "pm02": 5.3, "pm10": 6.0, "pm02Compensated": 4.2, "pm003Count": 812.5, "atmp": 21.4,
           "atmpCompensated": 20.9, "rhum": 40.1, "rhumCompensated": 44.3, "tvocIndex": 98, "noxIndex": 1,
           "rco2": 612, "boot": 17, "wifi": -61, "channels": {"1": {"pm02": 5.1}, "2": {"pm02": 5.5}},
           "serialno": SERIAL, "firmware": "3.7.1", "model": "I-9PSL"}


def test_maps_a_real_reply_to_the_cloud_shape():
    row = localnet.map_reading(PAYLOAD, INDOOR, NOW)
    assert row["pm02_corrected"] == PAYLOAD["pm02Compensated"]
    assert row["atmp_corrected"] == 20.9 and row["rhum_corrected"] == 44.3
    assert row["firmwareVersion"] == "3.7.1"
    assert row["via"] == "local" and row["locationType"] == "indoor"
    assert row["locationId"] == 1 and row["locationName"] == "Living room"
    assert "channels" not in row and "boot" not in row and "pm02Compensated" not in row
    assert row["timestamp"] == "2026-11-22T15:00:00Z"


def test_wrong_serial_or_no_compensated_pm_is_no_row():
    assert localnet.map_reading({**PAYLOAD, "serialno": "aaaaaaaaaaaa"}, INDOOR, NOW) is None
    assert localnet.map_reading({k: v for k, v in PAYLOAD.items() if k != "pm02Compensated"}, INDOOR, NOW) is None
    assert localnet.map_reading({**PAYLOAD, "serialno": SERIAL.upper()}, INDOOR, NOW) is not None


def test_out_of_range_values_are_dropped():
    row = localnet.map_reading({**PAYLOAD, "rco2": 65535}, INDOOR, NOW)
    assert "rco2" not in row and row["pm02_corrected"] == 4.2
    assert localnet.map_reading({**PAYLOAD, "pm02Compensated": -5}, INDOOR, NOW) is None


# --- the read ---------------------------------------------------------------------------------------------------

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts import fake_airgradient as fake


@pytest.fixture
def local_monitor():
    """Start a fake monitor on loopback; the test gets (port, stop)."""
    started = []

    def start(**kwargs):
        server = fake.make_local_server(0, kwargs.pop("serial", SERIAL), **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server.server_address[1]

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def test_read_gives_up_on_a_slow_monitor_within_4_s(local_monitor):
    port = local_monitor(mode="slow")
    began = time.monotonic()
    with pytest.raises(localnet.LocalReadError):
        localnet.read_current(INDOOR, "127.0.0.1", port)
    assert time.monotonic() - began < 4.0


def serve_one_reply(status, kind, body):
    """A loopback server that answers every request with one fixed reply (a router's page, a redirect)."""
    class Wrong(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Location", "http://127.0.0.1:1/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Wrong)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("reply", [
    (200, "text/html", b"<html>router</html>"),
    (302, "application/json", b"{}"),
    (200, "application/json", b'{"a": "' + b"x" * 20000 + b'"}'),
], ids=["html", "redirect", "big body"])
def test_read_refuses_html_redirects_and_big_bodies(reply):
    server = serve_one_reply(*reply)
    try:
        with pytest.raises(localnet.LocalReadError):
            localnet.read_current(INDOOR, "127.0.0.1", server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def test_read_uses_the_spec_timeouts():
    seen = {}

    class Raw:
        def __init__(self):
            self.left = [b"{}"]

        def read1(self, size):
            return self.left.pop() if self.left else b""

        def close(self):
            pass

    class Reply:
        status_code, headers = 200, {"Content-Type": "application/json"}

        def __init__(self):
            self.raw = Raw()

        def close(self):
            pass

    def get(url, **kwargs):
        seen.update(kwargs, url=url)
        return Reply()

    localnet.read_current(INDOOR, "127.0.0.1", 80, get=get)
    assert seen["timeout"] == (2, 2) and seen["allow_redirects"] is False
    assert "token" not in seen["url"] and "token" not in (seen.get("params") or {})


def test_a_trickling_monitor_is_given_up_on_within_5_s(local_monitor):
    """One byte every 1.5 s never trips the 2 s read timeout; the 4 s total must. The total is checked as each byte
    arrives, so the read ends at the first arrival past 4 s (4.5 s here), never after the whole reply."""
    port = local_monitor(mode="trickle")
    began = time.monotonic()
    with pytest.raises(localnet.LocalReadError):
        localnet.read_current(INDOOR, "127.0.0.1", port)
    assert time.monotonic() - began < 5.0


def test_local_reads_ignore_proxy_settings(monkeypatch):
    """No HTTP_PROXY, system proxy or .netrc for an address on the home's network. Nothing is sent: send is stubbed."""
    import requests
    seen = {}

    def send(self, request, **kwargs):
        seen.update(kwargs)
        reply = requests.Response()
        reply.status_code, reply.headers["Content-Type"] = 200, "application/json"
        reply.raw = type("Raw", (), {"read1": lambda _, n, _left=[b"{}"]: _left.pop() if _left else b"",
                                     "close": lambda _: None, "release_conn": lambda _: None})()
        return reply

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setattr(requests.Session, "send", send)
    assert localnet.read_current(INDOOR, "192.0.2.7", 80) == {}
    assert not seen["proxies"]


def test_old_firmware_and_wrong_serial_never_map(local_monitor):
    for mode in ("old_firmware", "wrong_serial"):
        port = local_monitor(mode=mode)
        payload = localnet.read_current(INDOOR, "127.0.0.1", port)
        assert isinstance(payload, dict)
        assert localnet.map_reading(payload, INDOOR, NOW) is None


# --- discovery --------------------------------------------------------------------------------------------------

def test_discovery_keeps_ipv4_sorted_readable_monitors():
    def browser(seconds):
        return [{"serial": "FE80AAAAAAAA", "model": "I-9PSL", "firmware": "3.7.1", "addresses": ["fe80::1"], "port": 80},
                {"serial": "84FCE600000B", "model": "O-1PST", "firmware": "3.7.1", "addresses": ["fe80::2", "192.168.1.21"],
                 "port": 80},
                {"serial": "84FCE600000A", "model": "I-9PSL", "firmware": "3.7.1", "addresses": ["192.168.1.20"], "port": 80}]

    found = localnet.discover(browser=browser, env={}, proc_version=Path("/nonexistent"))
    assert [f.serial for f in found] == ["84fce600000a", "84fce600000b"]
    assert [f.address for f in found] == ["192.168.1.20", "192.168.1.21"]
    assert all(":" not in f.address for f in found)


def test_discovery_drops_bad_serials_and_addresses_off_the_home_network():
    def entry(serial, address, port=80):
        return {"serial": serial, "model": "I-9PSL", "firmware": "3.7.1", "addresses": [address], "port": port}

    def browser(seconds):
        return [entry("84fce600000a", "192.168.1.20"), entry("../etc/passwd", "192.168.1.21"),
                entry("84fce600000c", "8.8.8.8"), entry("84fce600000d", "169.254.3.4"),
                entry("84fce600000e", "not an address"), entry("84fce600000f", "10.0.0.5", port=70000),
                entry("zz", "10.0.0.6"), entry("84fce6000010", "127.0.0.1")]

    found = localnet.discover(browser=browser, env={}, proc_version=Path("/nonexistent"))
    assert [f.serial for f in found] == ["84fce600000a", "84fce600000d", "84fce6000010"]


def test_config_and_discovery_share_the_network_check():
    import ipaddress
    import config
    assert config.is_own_network(ipaddress.IPv4Address("192.168.1.2"))
    assert not config.is_own_network(ipaddress.IPv4Address("1.1.1.1"))


class FakeInfo:
    def __init__(self, serial, address):
        self.properties = {b"serialno": serial.encode(), b"model": b"I-9PSL", b"fw_ver": b"3.7.1"}
        self.port = 80
        self._address = address

    def parsed_addresses(self, version):
        return [self._address]


def fake_zeroconf(monkeypatch, infos, resolve_seconds=0.0):
    """Replace zeroconf with a fake that "announces" every name in `infos` (name -> FakeInfo or an exception)."""
    import zeroconf

    class Zc:
        def __init__(self, *a, **k):
            pass

        def get_service_info(self, service, name, timeout):
            time.sleep(resolve_seconds)
            info = infos[name]
            if isinstance(info, Exception):
                raise info
            return info

        def close(self):
            pass

    class Browser:
        def __init__(self, zc, service, handlers):
            for name in infos:
                handlers[0](zc, service, name, None)

        def cancel(self):
            pass

    monkeypatch.setattr(zeroconf, "Zeroconf", Zc)
    monkeypatch.setattr(zeroconf, "ServiceBrowser", Browser)
    monkeypatch.setattr(localnet, "BROWSE_LISTEN_SECONDS", 0.05)


def test_one_name_that_fails_to_resolve_does_not_empty_discovery(monkeypatch):
    fake_zeroconf(monkeypatch, {"a._airgradient._tcp.local.": RuntimeError("boom"),
                                "b._airgradient._tcp.local.": FakeInfo("84fce600000b", "192.168.1.21")})
    found = localnet.discover(env={}, proc_version=Path("/nonexistent"))
    assert [f.serial for f in found] == ["84fce600000b"]


def test_a_late_resolve_still_returns_what_was_found_and_the_deadline_counts_from_the_start(monkeypatch):
    fake_zeroconf(monkeypatch, {"a._airgradient._tcp.local.": FakeInfo("84fce600000a", "192.168.1.20"),
                                "b._airgradient._tcp.local.": FakeInfo("84fce600000b", "192.168.1.21")},
                  resolve_seconds=0.8)
    began = time.monotonic()
    found = localnet.discover(seconds=1.0, env={}, proc_version=Path("/nonexistent"))
    assert [f.serial for f in found] == ["84fce600000a"]
    assert time.monotonic() - began < 1.0 + localnet.BROWSE_GRACE_SECONDS + 0.15


def test_not_readable_says_why():
    def found(model, firmware):
        return localnet.Found("84fce600000a", model, firmware, "192.168.1.20", 80)

    assert "older than 3.1.4" in localnet.not_readable(found("I-9PSL", "3.1.3"))
    assert "doesn't read this model" in localnet.not_readable(found("X-1", "3.7.1"))
    assert localnet.not_readable(found("I-9PSL", "3.7.1")) is None


def test_wsl_and_containers_never_browse(tmp_path):
    proc = tmp_path / "version"
    proc.write_text("Linux version 5.15.0-microsoft-standard-WSL2 (root@x) (gcc)")
    assert localnet.environment_blocker(env={}, proc_version=proc) == "wsl"

    def browser(seconds):
        pytest.fail("browsed")

    assert localnet.discover(browser=browser, env={}, proc_version=proc) == []


def test_a_host_built_from_the_dockerfile_is_hosted_not_a_container(tmp_path, monkeypatch):
    # Railway builds from the Dockerfile, so /.dockerenv may exist there: "container" would start the poller and the
    # history store on Home Air on a hosted server (local_reads_on only stops "hosted").
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    monkeypatch.setattr(localnet.token_setup, "DOCKER_MARKER", marker)
    assert localnet.environment_blocker(env={}, proc_version=tmp_path / "none") == "container"
    assert localnet.environment_blocker(env={"RAILWAY_ENVIRONMENT": "production"}, proc_version=tmp_path / "none") == "hosted"


def test_discovery_returns_within_3_5_s():
    never = threading.Event()
    began = time.monotonic()
    found = localnet.discover(browser=lambda seconds: never.wait(30) and [], env={}, proc_version=Path("/nonexistent"))
    assert found == [] and time.monotonic() - began < 3.5


# --- the poller -------------------------------------------------------------------------------------------------

import logging

import budgets


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_poller(monitors, read, find=lambda *a, **k: [], browse=True, **kwargs):
    clock = Clock()
    poller = localnet.Poller(monitors, browse, read=read, find=find, clock=clock, **kwargs)
    return poller, clock


def run(poller, clock, seconds):
    for _ in range(int(seconds)):
        poller.tick()
        clock.now += 1


def monitor(kind, serial, host="127.0.0.1"):
    return localnet.Monitor(kind, serial, kind, localnet.LOCAL_IDS[kind], host, 80)


def test_ten_minutes_read_each_monitor_ten_times():
    budgets.clear()
    calls, rows = [], []
    indoor, outdoor = monitor("indoor", "a" * 12), monitor("outdoor", "b" * 12)

    def read(mon, address, port):
        calls.append(mon.kind)
        return {**PAYLOAD, "serialno": mon.serial}

    poller, clock = make_poller([indoor, outdoor], read, on_row=rows.append)
    run(poller, clock, 600)
    assert 9 <= calls.count("indoor") <= 11 and 9 <= calls.count("outdoor") <= 11
    assert len(rows) == len(calls)
    assert poller.state("indoor") == "local" and poller.latest("outdoor")["via"] == "local"


def test_a_moved_monitor_is_found_again_after_three_misses():
    budgets.clear()
    seen = []
    mon = monitor("indoor", SERIAL, host="192.168.1.20")
    moved = localnet.Found(SERIAL, "I-9PSL", "3.7.1", "192.168.1.30", 80)

    def read(mon, address, port):
        seen.append(address)
        if address == "192.168.1.20":
            raise localnet.LocalReadError("ConnectTimeout")
        return PAYLOAD

    poller, clock = make_poller([mon], read, find=lambda *a, **k: [moved])
    run(poller, clock, 240)
    assert seen[:4] == ["192.168.1.20"] * 3 + ["192.168.1.30"]
    assert poller.state("indoor") == "local"


def test_a_monitor_not_found_pauses_ten_minutes():
    budgets.clear()
    seen = []

    def read(mon, address, port):
        seen.append(clock.now)
        raise localnet.LocalReadError("ConnectTimeout")

    poller, clock = make_poller([monitor("indoor", SERIAL)], read)
    run(poller, clock, 125)
    assert len(seen) == 3 and poller.state("indoor") == "stale"
    last = seen[-1]
    run(poller, clock, localnet.PAUSE_SECONDS - 5)
    assert len(seen) == 3
    run(poller, clock, 10)
    assert len(seen) == 4 and seen[-1] - last >= localnet.PAUSE_SECONDS


def test_failures_log_no_address(caplog):
    budgets.clear()

    def read(mon, address, port):
        raise localnet.LocalReadError("ConnectTimeout")

    poller, clock = make_poller([monitor("indoor", SERIAL)], read)
    with caplog.at_level(logging.DEBUG):
        run(poller, clock, 300)
    assert "indoor" in caplog.text and "127.0.0.1" not in caplog.text


def test_a_sleeping_computer_counts_as_a_gap_and_a_stale_reading():
    """The monotonic clock stops in sleep: only the wall clock sees the hours asleep."""
    budgets.clear()
    wall = [NOW]
    resumed = []
    poller, clock = make_poller([monitor("indoor", SERIAL)], lambda mon, a, p: PAUSE_PAYLOAD,
                                utcnow=lambda: wall[0], on_resume=lambda mon, gap: resumed.append(gap))
    poller.tick()
    assert poller.state("indoor") == "local"
    wall[0] = NOW + timedelta(hours=8)
    clock.now += localnet.LOCAL_READ_SECONDS   # monotonic: about a minute
    assert poller.state("indoor") == "stale"
    poller.tick()
    assert len(resumed) == 1 and resumed[0] >= 8 * 3600
    assert poller.state("indoor") == "local"


PAUSE_PAYLOAD = {**PAYLOAD, "serialno": SERIAL}


# --- the OS name lookup and the remembered address ---------------------------------------------------------------

import os
import stat

import address_memory

OUT_SERIAL = "d83bda1c6b64"
OUT_PAYLOAD = {**PAYLOAD, "serialno": OUT_SERIAL, "model": "O-1PST"}


def _indoor_only_browser(seconds):
    return [{"serial": SERIAL, "model": "I-9PSL", "firmware": "3.7.1", "addresses": ["192.168.1.20"], "port": 80}]


def _discover(resolver, reader, serials=(SERIAL, OUT_SERIAL)):
    return localnet.discover(browser=_indoor_only_browser, env={}, proc_version=Path("/nonexistent"),
                             serials=serials, resolver=resolver, reader=reader)


def test_a_monitor_missing_from_the_browse_is_found_by_the_os_lookup():
    asked = []

    def resolver(name):
        asked.append(name)
        return ["192.168.1.21"]

    found = _discover(resolver, lambda mon, address, port: OUT_PAYLOAD)
    assert asked == [f"airgradient_{OUT_SERIAL}.local"]   # the monitor the browse found is not looked up
    assert [(f.serial, f.address, f.via, f.model) for f in found] == [
        (SERIAL, "192.168.1.20", "mdns", "I-9PSL"), (OUT_SERIAL, "192.168.1.21", "name", "O-1PST")]


def test_the_os_lookup_refuses_off_network_addresses_and_other_monitors():
    assert [f.serial for f in _discover(lambda name: ["8.8.8.8"], lambda *a: OUT_PAYLOAD)] == [SERIAL]
    assert [f.serial for f in _discover(lambda name: ["192.168.1.21"], lambda *a: {**OUT_PAYLOAD, "serialno": "aaaaaaaaaaaa"})] == [SERIAL]

    def unreadable(*a):
        raise localnet.LocalReadError("ConnectTimeout")

    assert [f.serial for f in _discover(lambda name: ["192.168.1.21"], unreadable)] == [SERIAL]
    assert localnet.os_lookup("../x", lambda name: ["192.168.1.21"]) is None


def test_a_resolver_that_hangs_is_cut_off_within_the_limit():
    never = threading.Event()
    began = time.monotonic()
    address = localnet.os_lookup(OUT_SERIAL, lambda name: never.wait(30) or ["192.168.1.21"], seconds=0.3)
    assert address is None and time.monotonic() - began < 1.0
    never.set()


def test_a_failed_browse_keeps_a_working_address():
    budgets.clear()
    seen, ok = [], [False]
    mon = monitor("indoor", SERIAL, host="192.168.1.20")

    def read(mon, address, port):
        seen.append(address)
        if not ok[0]:
            raise localnet.LocalReadError("ConnectTimeout")
        return PAYLOAD

    poller, clock = make_poller([mon], read)   # find() finds nothing
    run(poller, clock, 125)
    assert len(seen) == 3
    ok[0] = True
    run(poller, clock, localnet.PAUSE_SECONDS + 5)
    assert seen[3:] and set(seen) == {"192.168.1.20"} and poller.state("indoor") == "local"


def test_the_poller_asks_to_look_for_its_serial():
    budgets.clear()
    asked = []

    def find(**kwargs):
        asked.append(kwargs["serials"])
        return []

    poller, clock = make_poller([monitor("indoor", SERIAL)], lambda *a: (_ for _ in ()).throw(localnet.LocalReadError("x")),
                                find=find)
    run(poller, clock, 125)
    assert asked == [[SERIAL]]


def test_others_found_are_listed_by_kind_without_address_or_full_serial():
    # "Your monitors" draws its switch from this: the read monitor, one it can't read and any address must stay out.
    budgets.clear()
    found = [localnet.Found(SERIAL, "I-9PSL", "3.7.1", "192.168.1.33", 80),
             localnet.Found("84fce6333333", "I-9PSL", "3.7.1", "192.168.1.34", 80),
             localnet.Found("84fce6444444", "I-9PSL", "3.1.3", "192.168.1.35", 80)]
    poller, clock = make_poller([monitor("indoor", SERIAL, host=None)], lambda mon, address, port: PAYLOAD,
                                find=lambda **k: found)
    run(poller, clock, 3)
    assert poller.others() == {"indoor": [{"serial_end": "3333", "model": "I-9PSL"}]}


def test_the_last_good_address_is_reused_after_a_restart_and_the_file_is_private(tmp_path):
    budgets.clear()
    path = tmp_path / "sub" / address_memory.ADDRESS_FILE
    mon = monitor("indoor", SERIAL, host=None)
    first, clock = make_poller([mon], lambda mon, address, port: PAYLOAD,
                               find=lambda **k: [localnet.Found(SERIAL, "I-9PSL", "3.7.1", "192.168.1.33", 80)],
                               memory=address_memory.AddressMemory(path))
    run(first, clock, 3)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert list(path.parent.iterdir()) == [path]   # no temp file left

    seen = []

    def read(mon, address, port):
        seen.append(address)
        return PAYLOAD

    def no_browse(**kwargs):
        raise AssertionError("browsed before trying the remembered address")

    second, clock = make_poller([mon], read, find=no_browse, memory=address_memory.AddressMemory(path))
    run(second, clock, 3)
    assert seen[0] == "192.168.1.33"


def test_a_monitor_remembered_on_another_port_is_read_there_after_a_restart(tmp_path):
    budgets.clear()
    path = tmp_path / address_memory.ADDRESS_FILE
    mon = monitor("indoor", SERIAL, host=None)
    first, clock = make_poller([mon], lambda mon, address, port: PAYLOAD,
                               find=lambda **k: [localnet.Found(SERIAL, "I-9PSL", "3.7.1", "192.168.1.33", 8123)],
                               memory=address_memory.AddressMemory(path))
    run(first, clock, 3)
    seen = []

    def read(mon, address, port):
        seen.append((address, port))
        return PAYLOAD

    def no_browse(**kwargs):
        raise AssertionError("browsed before trying the remembered address")

    second, clock = make_poller([mon], read, find=no_browse, memory=address_memory.AddressMemory(path))
    run(second, clock, 3)
    assert seen[0] == ("192.168.1.33", 8123)


def test_the_address_file_refuses_off_network_addresses(tmp_path):
    path = tmp_path / address_memory.ADDRESS_FILE
    memory = address_memory.AddressMemory(path)
    memory.remember(SERIAL, "8.8.8.8")
    memory.remember("not-a-serial", "192.168.1.5")
    assert not path.exists()
    path.write_text('{"%s": {"address": "8.8.8.8", "at": "x"}, "%s": {"address": "192.168.1.9", "at": "x"}}'
                    % (SERIAL, OUT_SERIAL))
    memory = address_memory.AddressMemory(path)
    assert memory.get(SERIAL) is None and memory.get(OUT_SERIAL) == ("192.168.1.9", 80)   # no port in an old file: 80
    memory.remember(SERIAL, "192.168.1.5", 0)
    memory.remember(SERIAL, "192.168.1.5", 70000)
    assert memory.get(SERIAL) is None
    path.write_text('{"%s": {"address": "192.168.1.9", "port": 99999, "at": "x"}}' % SERIAL)
    assert address_memory.AddressMemory(path).get(SERIAL) is None
    path.write_text("not json")
    assert address_memory.AddressMemory(path).get(OUT_SERIAL) is None
