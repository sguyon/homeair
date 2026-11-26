"""The skill's script (skills/home-air-quality/scripts/home_air.py): which address it asks, and what it says when the
port doesn't answer as Home Air does. Wrong here, an agent tells the user the server is down or out of date when it isn't."""
import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "home-air-quality" / "scripts" / "home_air.py"


def load():
    spec = importlib.util.spec_from_file_location("home_air", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def serve():
    """Starts a local server answering {path: body} (anything else is 404); returns its port."""
    servers = []

    def start(routes):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = routes.get(self.path)
                self.send_response(404 if body is None else 200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode() if body is not None else b"Not found")

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("localhost", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@pytest.fixture
def home_air(tmp_path, monkeypatch):
    monkeypatch.delenv("HOME_AIR_URL", raising=False)
    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED", raising=False)
    module = load()
    (tmp_path / "server.py").write_text("")   # a Home Air folder, never the real one (its app.port)
    monkeypatch.setattr(module, "HOME_AIR", tmp_path)
    return module


def test_a_recorded_port_that_does_not_answer_falls_back_to_the_default(home_air, serve, monkeypatch, tmp_path):
    monkeypatch.setattr(home_air, "DEFAULT_PORT", serve({"/api/agent/now": {"read": "ok"}}))
    (tmp_path / "app.port").write_text(f"{free_port()}\n")
    assert home_air.call("/api/agent/now") == {"read": "ok"}
    monkeypatch.setenv("HOME_AIR_URL", "http://example.invalid/")
    assert home_air.base_urls() == ["http://example.invalid"]   # set by the user: the only address
    monkeypatch.delenv("HOME_AIR_URL")
    (tmp_path / "agent-url").write_text("https://home.example/\n")   # ./homeair skill add --url: Home Air on another computer
    assert home_air.base_urls() == ["https://home.example"]


def test_a_404_says_out_of_date_only_when_the_port_is_home_air(home_air, serve, monkeypatch):
    monkeypatch.setattr(home_air, "DEFAULT_PORT", serve({"/api/current": [{"locationId": 1}]}))
    with pytest.raises(home_air.Fail, match="older than the skill"):
        home_air.call("/api/agent/now")
    monkeypatch.setattr(home_air, "DEFAULT_PORT", serve({}))
    with pytest.raises(home_air.Fail, match="Something else answers"):
        home_air.call("/api/agent/now")


def test_the_skill_alone_says_there_is_no_home_air_here(home_air, monkeypatch, tmp_path):
    # installed on its own (no Home Air folder around it, no address saved): "start it" would name a command that isn't there
    (tmp_path / "server.py").unlink()
    monkeypatch.setattr(home_air, "DEFAULT_PORT", free_port())
    with pytest.raises(home_air.Fail) as alone:
        home_air.call("/api/agent/now")
    assert "No Home Air answers" in str(alone.value) and "https://github.com/sguyon/homeair" in str(alone.value)
    (tmp_path / "server.py").write_text("")   # inside a Home Air folder: stopped, not missing
    with pytest.raises(home_air.Fail) as stopped:
        home_air.call("/api/agent/now")
    assert "Either Home Air is stopped" in str(stopped.value) and "github.com" not in str(stopped.value)
