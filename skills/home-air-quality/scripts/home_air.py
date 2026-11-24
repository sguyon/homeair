#!/usr/bin/env python3
"""Reads one home from a running Home Air server and prints compact JSON. Standard library only; read-only.

  home_air.py now                       current readings and the air read (actions with a why) in one call
  home_air.py history [--hours N] [--where indoor|outdoor]   the last N hours (default 12, 1 to 168), hourly

The server is HOME_AIR_URL; else the address `./homeair skill add --url` saved (agent-url in the Home Air folder);
else, when this skill sits in a Home Air folder (setup links it from there), the port that
`./homeair start` chose (app.port), then http://localhost:5555 if that one doesn't answer; else http://localhost:5555.
It asks the server's /api/agent/now and /api/agent/history, the same answers as Home Air's MCP tools. On failure it
prints {"error": ...} and exits 1.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 5555   # server.py's local port without PORT
TIMEOUT = 60          # seconds: the first read after a restart waits on weather and smoke sources.
DEFAULT_HOURS = 12    # hours of history when the question doesn't say: "last night" from the morning.
HOME_AIR = Path(__file__).resolve().parents[3]   # the Home Air folder, when this is its skills/home-air-quality/scripts
NO_NETWORK_VAR = "CODEX_SANDBOX_NETWORK_DISABLED"  # set by Codex when its sandbox blocks the network, localhost too
UPDATE = ("This Home Air is older than the skill and has no /api/agent routes. Update Home Air (git pull in the Home "
          "Air folder), then restart it (./homeair start).")
OTHER = ("Something else answers at {}, not Home Air. Set HOME_AIR_URL to Home Air's address, or check the port setup "
         "chose (app.port in the Home Air folder).")
SANDBOX = ("This command runs in a sandbox without network access ({} is set), which blocks localhost too, so the server "
           "may be running: run the command again with network access allowed (outside the sandbox, or in Codex "
           "network_access = true under [sandbox_workspace_write] in ~/.codex/config.toml).")
STOPPED_OR_SANDBOX = ("Either Home Air is stopped (start it: ./homeair start in the Home Air folder, or set "
                      "HOME_AIR_URL), or this command runs in a sandbox without network access, which blocks "
                      "localhost too (Codex's default sandbox does): then run the command again with network access "
                      "allowed.")
REPO = "https://github.com/sguyon/homeair"   # where Home Air and its setup live, for a skill installed on its own
ALONE = ("No Home Air answers at {}. This skill reads a running Home Air, and the skill's files aren't in a Home Air "
         "folder. If Home Air is installed on this computer, start it (./homeair start in its folder), and set HOME_AIR_URL "
         "to the address ./homeair status shows if it isn't http://localhost:5555. If it runs on a home server or online, "
         "set HOME_AIR_URL to its address. To install Home Air: " + REPO)


class Fail(Exception):
    pass


def base_urls():
    """The addresses to try, in order: HOME_AIR_URL alone; else the port setup chose, then the default port."""
    url = os.environ.get("HOME_AIR_URL", "").strip()
    if not url:
        try:   # the address `./homeair skill add --url` saved: Home Air online or on a home server
            url = (HOME_AIR / "agent-url").read_text().strip()
        except OSError:
            url = ""
    if url:
        return [url.rstrip("/")]
    urls = []
    if (HOME_AIR / "server.py").is_file():
        try:
            urls.append(f"http://localhost:{int((HOME_AIR / 'app.port').read_text().strip())}")
        except (OSError, ValueError):
            pass
    return list(dict.fromkeys(urls + [f"http://localhost:{DEFAULT_PORT}"]))


def is_home_air(base):
    """Whether `base` answers /api/current as Home Air does: a list of monitors, or {"state": ...} / {"error": ...}."""
    try:
        with urllib.request.urlopen(base + "/api/current", timeout=TIMEOUT) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:   # a 502 from AirGradient still comes as Home Air's JSON
        try:
            body = json.loads(e.read().decode())
        except ValueError:
            return False
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
    return isinstance(body, list) or (isinstance(body, dict) and ("state" in body or "error" in body))


def call(path):
    tried = []
    for base in base_urls():
        try:
            with urllib.request.urlopen(base + path, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise Fail(UPDATE if is_home_air(base) else OTHER.format(base))
            try:
                body = json.loads(e.read().decode())
            except ValueError:
                body = {}
            raise Fail(body.get("error") if isinstance(body, dict) and body.get("error") else f"{path}: HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            tried.append(f"{base} ({getattr(e, 'reason', e)})")
        except ValueError:
            raise Fail(f"{base} answered, but not as Home Air does. Check HOME_AIR_URL.")
    sandboxed = os.environ.get(NO_NETWORK_VAR)
    alone = not os.environ.get("HOME_AIR_URL", "").strip() and not (HOME_AIR / "server.py").is_file()
    if alone and not sandboxed:
        raise Fail(ALONE.format(", ".join(tried)))
    raise Fail(f"Home Air is not answering at {', '.join(tried)}. "
               + (SANDBOX.format(NO_NETWORK_VAR) if sandboxed else STOPPED_OR_SANDBOX))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("now")
    h = sub.add_parser("history")
    h.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    h.add_argument("--where", choices=("indoor", "outdoor"), default="indoor")
    a = p.parse_args()
    path = "/api/agent/now" if a.cmd == "now" else \
        "/api/agent/history?" + urllib.parse.urlencode({"hours": a.hours, "where": a.where})
    try:
        result = call(path)
    except Fail as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
