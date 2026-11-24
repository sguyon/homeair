"""setup.py --start (14 Nov, after Codex ran the live check before the background app was up)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import setup  # noqa: E402

SERVER = """#!/bin/sh
exec "$1" -c "
import http.server, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
open('app.pid', 'w').write(str(os.getpid()))
http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
" 
"""


def fake_root(tmp_path, script):
    run = tmp_path / "scripts" / "run.sh"
    run.parent.mkdir()
    run.write_text(script)
    run.chmod(0o755)
    return tmp_path


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


def test_start_waits_for_the_app_and_prints_the_address_and_where_to_connect(tmp_path, capsys):
    root = fake_root(tmp_path, SERVER.replace('"$1"', f'"{sys.executable}"'))
    first, second = free_port(), free_port()   # fixed ports collide when several checkouts run the tests at once
    env = {"PORT": first, "PATH": "/usr/bin:/bin"}
    assert setup.start_app(env, root) == 0
    out = capsys.readouterr().out
    assert f"Started: http://localhost:{first}" in out
    assert f"Add your AirGradient API token at http://localhost:{first}/connect" in out
    import urllib.request
    assert urllib.request.urlopen(f"http://127.0.0.1:{first}/").status == 200
    (tmp_path / "b").mkdir()
    other = fake_root(tmp_path / "b", SERVER.replace('"$1"', f'"{sys.executable}"'))
    assert setup.start_app({**env, "PORT": second, "AIRGRADIENT_TOKEN": "invented"}, other) == 0   # a saved token: no connect line
    assert "/connect" not in capsys.readouterr().out
    import os, signal
    for started in (root, other):   # only the apps this test started, by their pid file, never whatever holds a port
        os.kill(int((started / "app.pid").read_text().split()[0]), signal.SIGTERM)


def test_a_bare_live_checks_the_port_start_chose(tmp_path):
    assert setup.started_port({"PORT": "5555"}, tmp_path) == 5555
    (tmp_path / "app.port").write_text("5601\n")
    assert setup.started_port({"PORT": "5555"}, tmp_path) == 5601


def test_start_says_so_when_the_app_dies(tmp_path, capsys):
    root = fake_root(tmp_path, "#!/bin/sh\necho boom >&2\nexit 3\n")
    assert setup.start_app({"PORT": "5642", "PATH": "/usr/bin:/bin"}, root) == 1
    out = capsys.readouterr().out
    assert "stopped right after starting" in out and "boom" in out


def test_a_second_run_restarts_the_login_items_copy_and_app_port_names_it(tmp_path, monkeypatch, capsys):
    """Setup run again while the login item serves: no second copy on another port (two copies read the monitors
    twice and write one history file), the system restarts its copy so it reads the settings just written, and
    app.port names that port: --live, --connect and --status ask app.port's."""
    import http.server
    import os
    import subprocess
    import threading
    import pytest
    import autostart

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    page = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)   # the login item's copy (stubbed: no real login item)
    threading.Thread(target=page.serve_forever, daemon=True).start()
    port = page.server_address[1]
    root, home = tmp_path / "repo", tmp_path / "home"
    (root / "scripts").mkdir(parents=True)
    autostart.plist_path(home).parent.mkdir(parents=True)
    autostart.plist_path(home).write_text(autostart.launchd_plist(root, home, port))
    (root / autostart.PID_FILE).write_text("111\n")
    (root / setup.PORT_FILE).write_text("5600\n")   # an earlier run's copy on a fallback port, gone since
    system = []

    def run(cmd, **k):
        system.append(cmd)
        if cmd[:2] == ["launchctl", "kickstart"]:
            (root / autostart.PID_FILE).write_text("222\n")   # the system's new process
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(setup.subprocess, "run", run)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *a, **k: pytest.fail("a second copy was started"))
    monkeypatch.setattr(autostart, "stop_setup_copy", lambda root: pytest.fail("the login item's copy was stopped by its pid"))
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}   # no PORT: the login item's port comes from its own file
    try:
        assert setup.start_app(env, root) == 0
        assert system == [["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{autostart.LABEL}"]]
        assert setup.started_port(env, root) == port
        assert f"Restarted: http://localhost:{port}" in capsys.readouterr().out
        (root / setup.PORT_FILE).write_text("5600\n")
        monkeypatch.setattr(autostart, "turn_on", lambda *a: 0)   # turning it on again for this port succeeded
        assert setup.start_at_login("on", {**env, "PORT": str(port)}, root) == 0
        assert setup.started_port(env, root) == port
    finally:
        page.shutdown()
        page.server_close()
