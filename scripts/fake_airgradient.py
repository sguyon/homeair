"""AirGradient stand-in for screenshots and tests only; all readings are made up.

    python3 scripts/fake_airgradient.py <port> [--empty]
    python3 scripts/fake_airgradient.py <port> --local --serial <12 hex> [--model O-1PST] [--firmware 3.1.3]
                                        [--slow | --wrong-serial | --old-firmware]
    either: [--log FILE]   cloud only: [--monitors FILE]

Accepts only TEST_TOKEN. Serves current and past readings from setup_monitors.json;
--empty represents a connected account with no monitors. Binds to loopback only.

--local is a monitor on the Wi-Fi instead: GET /measures/current and GET /config, no token, made-up values.
--slow waits 5 s before answering, --wrong-serial answers another serial, --old-firmware answers firmware 3.1.3
without pm02Compensated. "Down" is no fake on the port; "moved" is a second fake on another port.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from time import sleep
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TEST_TOKEN = "setup-screenshot-test-token"  # Public, made-up credential; never an AirGradient key.
MONITORS = Path(__file__).with_name("setup_monitors.json")
STEP = timedelta(minutes=5)  # AirGradient's past readings are spaced five minutes apart.
DEFAULT_RANGE = timedelta(hours=24)  # History range when from/to are omitted.
API_PATH = "/public/api/v1/locations"
SLOW_SECONDS = 5  # s: a local monitor in --slow mode waits this long, past the reader's 4 s limit (2 s connect + 2 s read).
TRICKLE_SECONDS = 1.5  # s between the bytes a local monitor in trickle mode sends: each is inside the 2 s read timeout.
OTHER_SERIAL = "000000000000"  # What a local monitor in --wrong-serial mode answers.
LOCAL_CONFIG = {"country": "US", "temperatureUnit": "f", "pmStandard": "ugm3"}  # What a monitor's /config holds that we read.


def reading(monitor, at):
    return {**monitor, "timestamp": at.strftime("%Y-%m-%dT%H:%M:%S.000Z")}


def record(server, path):
    """With --log, one line per request: the path without its query (a token never reaches the file)."""
    log = getattr(server, "log", None)
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(urlparse(path).path + "\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        record(self.server, self.path)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if query.get("token") != [TEST_TOKEN]:
            return self.send(401, {"message": "Unauthorized"})
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if url.path == f"{API_PATH}/measures/current":
            return self.send(200, [reading(m, now) for m in self.server.monitors])
        parts = url.path.split("/")
        if url.path.startswith(API_PATH + "/") and len(parts) == 8 and parts[-2:] == ["measures", "past"]:
            monitor = next((m for m in self.server.monitors if str(m["locationId"]) == parts[-3]), None)
            if monitor is None:
                return self.send(404, {"message": "Location not found"})
            end = datetime.fromisoformat(query["to"][0]) if query.get("to") else now
            at = datetime.fromisoformat(query["from"][0]) if query.get("from") else end - DEFAULT_RANGE
            rows = []
            while at <= min(end, now):
                rows.append(reading(monitor, at))
                at += STEP
            return self.send(200, rows)
        return self.send(404, {"message": "Not found"})

    def send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class LocalHandler(BaseHTTPRequestHandler):
    """A monitor on the Wi-Fi: the keys of the firmware's /measures/current (made-up values) and its /config."""

    send = Handler.send
    log_message = Handler.log_message

    def do_GET(self):
        server = self.server
        record(server, self.path)
        if server.mode == "slow":
            sleep(SLOW_SECONDS)
        if server.mode == "trickle":   # a valid reply, one byte at a time
            data = json.dumps({"pm02Compensated": 4.2, "serialno": server.serial}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                for i in range(len(data)):
                    self.wfile.write(data[i:i + 1])
                    self.wfile.flush()
                    sleep(TRICKLE_SECONDS)
            except OSError:   # the reader gave up
                pass
            return
        if self.path == "/config":
            return self.send(200, LOCAL_CONFIG)
        if self.path != "/measures/current":
            return self.send(404, {"message": "Not found"})
        body = {"pm01": 3.1, "pm02": 5.3, "pm10": 6.0, "pm02Compensated": 4.2, "pm003Count": 812.5, "atmp": 21.4,
                "atmpCompensated": 20.9, "rhum": 40.1, "rhumCompensated": 44.3, "tvocIndex": 98, "noxIndex": 1,
                "rco2": 612, "boot": 17, "wifi": -61, "serialno": server.serial, "firmware": server.firmware,
                "model": server.model}
        if server.mode == "wrong_serial":
            body["serialno"] = OTHER_SERIAL
        if server.mode == "old_firmware":
            body["firmware"] = "3.1.3"
            del body["pm02Compensated"]
        return self.send(200, body)


def make_local_server(port, serial, model="I-9PSL", firmware="3.7.1", mode=None, log=None):
    """A fake monitor on loopback; mode is None, "slow", "trickle", "wrong_serial" or "old_firmware"."""
    server = ThreadingHTTPServer(("127.0.0.1", port), LocalHandler)
    server.daemon_threads = True
    server.block_on_close = False  # a --slow answer still in flight must not hold up closing
    server.serial, server.model, server.firmware, server.mode = serial, model, firmware, mode
    server.log = log
    return server


def make_server(port, *, empty=False, monitors=MONITORS, log=None):
    server = HTTPServer(("127.0.0.1", port), Handler)
    server.monitors = [] if empty else json.loads(Path(monitors).read_text(encoding="utf-8"))
    server.log = log
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--serial")
    parser.add_argument("--model", default="I-9PSL")
    parser.add_argument("--firmware", default="3.7.1")
    parser.add_argument("--monitors", default=MONITORS, help="the cloud's monitors, a JSON list (rows may carry serialno)")
    parser.add_argument("--log", help="append each request's path to this file")
    modes = parser.add_mutually_exclusive_group()
    for flag in ("slow", "wrong-serial", "old-firmware"):
        modes.add_argument(f"--{flag}", dest="mode", action="store_const", const=flag.replace("-", "_"))
    args = parser.parse_args()
    if args.local:
        if not args.serial:
            parser.error("--local needs --serial")
        with make_local_server(args.port, args.serial, args.model, args.firmware, args.mode, args.log) as server:
            server.serve_forever()
        raise SystemExit
    with make_server(args.port, empty=args.empty, monitors=args.monitors, log=args.log) as server:
        server.serve_forever()
