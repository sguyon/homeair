from pathlib import Path
"""Local first-run token entry; all credentials and files here are made up."""
import logging
from html import unescape
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

import server
import sources

TOKEN = "made-up-airgradient-token"
REAL_GET = requests.get   # the fake cloud tests send real requests to it, on loopback
BASE = "http://localhost:5555"


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "runtime", server)
    monkeypatch.setattr(server, "TOKEN", "")
    monkeypatch.setattr(server, "MODE", "no_token")
    (tmp_path / "home").mkdir()
    (tmp_path / "app").mkdir()   # the code file sits apart here, so tests can list the .env folder alone
    monkeypatch.setattr(server, "env_path", tmp_path / "home" / ".env")
    monkeypatch.setattr(server, "code_path", tmp_path / "app" / server.token_setup.CODE_FILE)
    monkeypatch.setenv("AIRGRADIENT_TOKEN", "")
    # Explicit literals keep the test independent of the production allow/deny lists.
    for marker in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "VERCEL", "RENDER", "FLY_APP_NAME", "K_SERVICE", "DYNO",
                   "KUBERNETES_SERVICE_HOST", "KOYEB_APP_NAME", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setattr(server.token_setup, "DOCKER_MARKER", tmp_path / "no-container")
    monkeypatch.setattr(server.token_setup, "PODMAN_MARKER", tmp_path / "no-podman")
    monkeypatch.setattr(server, "token_state", server.token_setup.TokenState())
    server.app.config["TESTING"] = True
    def get(url, **kwargs):
        assert url == sources.API_BASE + "/locations/measures/current"
        assert kwargs["params"] == {"token": TOKEN}
        return Reply()
    monkeypatch.setattr(sources.requests, "get", get)
    with server.app.test_client() as client:
        login(client)
        yield client
    sources._cache.clear()


def login(client, base=BASE, **kwargs):
    """Open the private link setup prints: it sets the owner cookie the box and the save require."""
    code = server.token_setup.owner_code(server.code_path)
    response = client.get("/connect?code=" + code, base_url=base, **kwargs)
    assert response.status_code == 200 and server.token_setup.OWNER_COOKIE in response.headers["Set-Cookie"]
    return response


class Reply:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return [{"locationId": 1, "model": "I-9PSL", "pm02": 3}]


def page(client, **kwargs):
    # The token box is at /connect (and at / while nothing else can be shown), for a browser with the owner cookie.
    return client.get("/connect", base_url=BASE, follow_redirects=True, **kwargs)


def nonce(response):
    match = re.search(r'name="setup_nonce" value="([^"]+)"', response.get_data(as_text=True))
    assert match, "the local page must include a one-use form value"
    return match[1]


def submit(client, value=None, token=TOKEN, **kwargs):
    value = value or nonce(page(client))
    return client.post("/api/token", base_url=BASE, data={"setup_nonce": value, "token": token},
                       headers=kwargs.pop("headers", {"Origin": BASE, "Referer": BASE + "/"}), **kwargs)


def test_local_form_is_visible_and_private(local):
    response = page(local)
    text = response.get_data(as_text=True)
    assert 'id="no-token" hidden' not in text
    assert 'type="password"' in text and 'autocomplete="off"' in text
    assert 'action="/api/token"' in text
    assert "Connect your AirGradient account" in text and "data-1p-ignore" in text
    # "Show me where": closed by default, the picture is in the repo and described for screen readers
    assert '<details class="token-help">' in text and "Picture of AirGradient" in text and 'src="/static/airgradient-api-access.png?v=' in text and "alt=" in text
    assert (Path(server.__file__).parent / "static" / "airgradient-api-access.png").exists()
    assert "https://app.airgradient.com/settings/place?tab=4" in text
    assert "Your agent never sees it" in text and "Landed on the dashboard" in text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    nonce(response)


@pytest.mark.parametrize("base,remote", [("http://127.0.0.1:5555", "127.0.0.1"), ("http://[::1]:5555", "::1")])
def test_loopback_addresses_work(local, base, remote):
    login(local, base, environ_overrides={"REMOTE_ADDR": remote})   # cookies belong to the name typed
    response = local.get("/connect", base_url=base, environ_overrides={"REMOTE_ADDR": remote})
    value = nonce(response)
    response = local.post("/api/token", base_url=base, environ_overrides={"REMOTE_ADDR": remote},
                          headers={"Origin": base}, data={"setup_nonce": value, "token": TOKEN})
    assert response.status_code == 303


def test_nonlocal_requests_never_get_or_submit_form(local):
    value = nonce(page(local))
    kwargs = {"environ_overrides": {"REMOTE_ADDR": "192.168.1.8"}}
    assert 'type="password"' not in page(local, **kwargs).get_data(as_text=True)
    assert submit(local, value, **kwargs).status_code == 403
    assert not server.env_path.exists()


def test_local_page_shows_the_box_to_the_owner(local):
    """The owner cookie lasts a year, so the box also opens after a restart by the login item. A one-use value and an
    HttpOnly Strict cookie come with the page itself."""
    response = local.get("/", base_url=BASE)   # no_token: the page is the box
    assert 'type="password"' in response.get_data(as_text=True)
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert submit(local).status_code == 303


@pytest.mark.parametrize("host", ["evil.example:5555", "custom.local:5555", "localhost", "localhost:5556", "127.0.0.1.evil.example:5555",
                                  "localhost.evil.example:5555", "[::2]:5555"])
def test_a_rebound_name_gets_no_box(local, host):
    """A web page that points its own name at this computer reaches the app from a loopback peer; the Host it sends is the
    page's name, so it gets neither the box nor a save."""
    value = nonce(page(local))
    headers = {"Host": host}
    for path in ("/", "/connect"):
        assert 'type="password"' not in local.get(path, base_url="http://" + host, headers=headers, follow_redirects=True).get_data(as_text=True)
    response = local.post("/api/token", base_url="http://" + host, data={"setup_nonce": value, "token": TOKEN},
                          headers={"Origin": "http://" + host, "Host": host})
    assert response.status_code == 403
    assert not server.env_path.exists()


def test_the_port_is_the_apps_own(local, monkeypatch):
    monkeypatch.setenv("PORT", "5601")
    assert 'type="password"' not in page(local).get_data(as_text=True)
    base = "http://localhost:5601"
    value = nonce(local.get("/connect", base_url=base))
    assert local.post("/api/token", base_url=base, headers={"Origin": base}, data={"setup_nonce": value, "token": TOKEN}).status_code == 303


def test_network_instructions(local):
    text = page(local, environ_overrides={"REMOTE_ADDR": "192.168.1.8"}).get_data(as_text=True)
    assert "computer it runs on" in text and "/connect" in text and "link the app printed" not in text


@pytest.mark.parametrize("marker", ["RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "VERCEL", "RENDER", "FLY_APP_NAME", "K_SERVICE", "DYNO",
                                  "KUBERNETES_SERVICE_HOST", "KOYEB_APP_NAME", "WEBSITE_INSTANCE_ID"])
def test_hosted_copies_block_entry_even_with_empty_marker(local, monkeypatch, marker):
    value = nonce(page(local))
    monkeypatch.setenv(marker, "")
    text = page(local).get_data(as_text=True)
    assert 'type="password"' not in text
    if marker.startswith("RAILWAY"):
        assert "Add AIRGRADIENT_TOKEN in your Railway project → Variables, then redeploy." in text
    else:
        assert ".env" in text and ".env" in text
    assert submit(local, value).status_code == 403


@pytest.mark.parametrize("marker", ["DOCKER_MARKER", "PODMAN_MARKER"])
def test_container_instructions_and_guard(local, marker):
    value = nonce(page(local))
    getattr(server.token_setup, marker).touch()
    text = page(local).get_data(as_text=True)
    assert 'type="password"' not in text
    assert "docker-compose.yml" in text and "docker compose restart" in text
    assert submit(local, value).status_code == 403


def test_unwritable_folder_instructions_and_guard(local):
    value = nonce(page(local))
    folder = server.env_path.parent
    folder.chmod(0o500)
    try:
        text = page(local).get_data(as_text=True)
        assert 'type="password"' not in text
        assert ".env" in text
        assert submit(local, value).status_code == 403
    finally:
        folder.chmod(0o700)


@pytest.mark.parametrize("where", ["runtime", "environment", "file"])
def test_existing_token_cannot_be_replaced(local, monkeypatch, where):
    value = nonce(page(local))
    if where == "runtime":
        monkeypatch.setattr(server, "TOKEN", "already-set")
    elif where == "environment":
        monkeypatch.setenv("AIRGRADIENT_TOKEN", "already-set")
    else:
        server.env_path.write_text("AIRGRADIENT_TOKEN=already-set\n")
    assert 'type="password"' not in page(local).get_data(as_text=True)
    assert submit(local, value).status_code == 303
    assert server.TOKEN != TOKEN


@pytest.mark.parametrize("headers", [{}, {"Origin": "null"}, {"Origin": "null", "Referer": BASE + "/"}, {"Origin": "http://evil.example"},
    {"Origin": "http://localhost:5556"}, {"Origin": "https://localhost:5555"},
    {"Referer": BASE + ".evil.example/"}, {"Origin": BASE, "Referer": "http://evil.example/"}])
def test_cross_origin_and_missing_origin_are_refused(local, headers):
    assert submit(local, headers=headers).status_code == 403
    assert not server.env_path.exists()


def test_same_origin_referer_fallback(local):
    assert submit(local, headers={"Referer": BASE + "/"}).status_code == 303


def test_nonce_is_one_use_and_bound_to_browser_and_origin(local):
    value = nonce(page(local))
    with server.app.test_client() as stranger:   # another browser of the owner's: its own cookie doesn't fit this form
        login(stranger)
        assert submit(stranger, value).status_code == 403
    assert submit(local, "wrong-value").status_code == 403
    assert submit(local, value, headers={"Origin": BASE, "Host": "127.0.0.1:5555"}).status_code == 403
    assert submit(local, value).status_code == 303
    assert submit(local, value).status_code == 303


def test_wrong_token_is_not_saved_or_logged_and_nonce_is_consumed(local, monkeypatch, caplog, capsys):
    class Wrong(Reply):
        status_code = 401
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Wrong())
    value = nonce(page(local))
    caplog.set_level(logging.DEBUG)
    response = submit(local, value)
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    assert "AirGradient refused this token" in unescape(response.get_data(as_text=True))
    assert TOKEN not in response.get_data(as_text=True) + caplog.text + str(capsys.readouterr())
    assert not server.env_path.exists() and server.TOKEN == ""
    assert submit(local, value).status_code == 403
    nonce(response)  # retry uses a fresh form value, and an empty password field


def test_failed_attempts_are_limited_even_across_pages(local, monkeypatch):
    class Wrong(Reply):
        status_code = 403
    calls = []
    def wrong(*a, **k):
        calls.append(1)
        return Wrong()
    monkeypatch.setattr(sources.requests, "get", wrong)
    for _ in range(5):
        assert submit(local).status_code == 400
    response = submit(local)
    assert response.status_code == 429 and len(calls) == 5
    assert "Retry-After" in response.headers
    now = server.token_setup.time.monotonic()
    monkeypatch.setattr(server.token_setup.time, "monotonic", lambda: now + 301)
    assert submit(local).status_code == 400


def test_save_preserves_other_lines_is_private_and_switches_running_server(local, monkeypatch, caplog, capsys):
    server.env_path.write_text("# private settings\nOTHER=keep\nAIRGRADIENT_TOKEN=your_token_here\n")
    server.env_path.chmod(0o644)
    response = submit(local)
    assert response.status_code == 303 and response.headers["Location"] == "/"
    assert response.headers["Cache-Control"] == "no-store"
    assert server.env_path.read_text() == f"# private settings\nOTHER=keep\nAIRGRADIENT_TOKEN={TOKEN}\n"
    assert stat.S_IMODE(server.env_path.stat().st_mode) == 0o600
    assert list(server.env_path.parent.iterdir()) == [server.env_path]
    assert server.TOKEN == TOKEN and os.environ["AIRGRADIENT_TOKEN"] == TOKEN and server.MODE == "live"
    sources._cache.clear()
    assert local.get("/api/current").get_json()[0]["locationId"] == 1
    assert local.get("/health").get_json()["airgradient_token_set"] is True
    assert "no_token" not in page(local).get_data(as_text=True)
    assert TOKEN not in response.get_data(as_text=True) + page(local).get_data(as_text=True) + caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize("failure", [requests.ConnectionError("secret=" + TOKEN), ValueError(TOKEN)])
def test_upstream_failures_are_plain_and_save_nothing(local, monkeypatch, failure, caplog):
    def fail(*a, **k):
        raise failure
    monkeypatch.setattr(sources.requests, "get", fail)
    response = submit(local)
    assert response.status_code == 502
    assert TOKEN not in response.get_data(as_text=True) + caplog.text
    assert not server.env_path.exists()


def test_disk_failure_does_not_install_token(local, monkeypatch, caplog):
    def fail(*a, **k):
        raise OSError(TOKEN)
    monkeypatch.setattr(server, "write_env_key", fail)
    response = submit(local)
    assert response.status_code == 500
    assert TOKEN not in response.get_data(as_text=True) + caplog.text
    assert server.TOKEN == "" and server.MODE == "no_token"


def test_demo_has_no_token_form_or_endpoint(local, monkeypatch):
    monkeypatch.setattr(server, "MODE", "demo")
    assert 'type="password"' not in page(local).get_data(as_text=True)
    assert submit(local, "anything").status_code == 403


@pytest.mark.parametrize("token", ["", "your_token_here", "two words", "bad\nOTHER=value", "'quoted'", "x" * 4097])
def test_malformed_tokens_never_call_airgradient(local, monkeypatch, token):
    def unexpected(*a, **k):
        pytest.fail("Malformed tokens must not reach the network")
    monkeypatch.setattr(sources.requests, "get", unexpected)
    response = local.post("/api/token", base_url=BASE, headers={"Origin": BASE},
                          data={"setup_nonce": nonce(page(local)), "token": token})
    assert response.status_code == 400
    assert not server.env_path.exists()


def test_missing_and_expired_form_values_are_refused(local, monkeypatch):
    assert local.post("/api/token", base_url=BASE, headers={"Origin": BASE}, data={"token": TOKEN}).status_code == 403
    value = nonce(page(local))
    now = server.token_setup.time.monotonic()
    monkeypatch.setattr(server.token_setup.time, "monotonic", lambda: now + 601)
    assert submit(local, value).status_code == 403


def test_post_body_size_is_bounded(local):
    response = local.post("/api/token", base_url=BASE, headers={"Origin": BASE},
                          data={"setup_nonce": nonce(page(local)), "token": "x" * 10000})
    assert response.status_code == 413 and not server.env_path.exists()


def test_env_symlink_does_not_expose_entry(local, tmp_path):
    target = tmp_path / "other-file"
    target.write_text("OTHER=keep\n")
    server.env_path.symlink_to(target)
    assert 'type="password"' not in page(local).get_data(as_text=True)
    assert submit(local, "anything").status_code == 403
    assert target.read_text() == "OTHER=keep\n"


def test_another_writer_winning_during_validation_is_not_overwritten(local, monkeypatch):
    def other_writer(*a, **k):
        server.env_path.write_text("AIRGRADIENT_TOKEN=from-terminal\n")
        return Reply()
    monkeypatch.setattr(sources.requests, "get", other_writer)
    assert submit(local).status_code == 303
    assert server.env_path.read_text() == "AIRGRADIENT_TOKEN=from-terminal\n"
    assert server.TOKEN == ""


def test_failed_atomic_replace_keeps_original_and_cleans_temp_files(local, monkeypatch):
    import config
    server.env_path.write_text("OTHER=keep\n")
    def fail(*a):
        raise OSError("disk failure")
    monkeypatch.setattr(config.os, "replace", fail)
    assert submit(local).status_code == 500
    assert server.env_path.read_text() == "OTHER=keep\n"
    assert list(server.env_path.parent.iterdir()) == [server.env_path]


def test_empty_account_is_a_valid_token(local, monkeypatch):
    class Empty(Reply):
        def json(self):
            return []
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Empty())
    assert submit(local).status_code == 303


def test_fresh_copy_with_an_empty_account_says_to_add_a_monitor(local, monkeypatch):
    from dataclasses import replace
    class Empty(Reply):
        def json(self):
            return []
    monkeypatch.setattr(server, "CFG", replace(server.CFG, demo=True))
    monkeypatch.setattr(server, "MODE", "demo")
    monkeypatch.delenv("DEMO", raising=False)
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Empty())
    assert submit(local).status_code == 303
    text = local.get("/", base_url=BASE).get_data(as_text=True)
    assert "Connected to AirGradient" in text and "No monitors with readings yet" in text and 'class="plist token-found"' not in text
    # one paragraph, the state first, at body size (design check, 13 Nov: the reason was a caption, quieter than its follow-up)
    assert ('<p class="token-next"><b>No monitors with readings yet.</b> A new monitor shows up here once it has sent its first reading: '
            'check it in the AirGradient app. You can close this tab and tell your agent.</p>') in text
    assert 'class="token-found-title">No monitors' not in text
    assert "data-wait-home" not in text   # setup can't go on without a monitor, so the page doesn't wait for the home


def test_the_setup_page_is_never_cached_even_in_a_fresh_copys_demo_mode(local, monkeypatch):
    """A fresh copy runs in demo mode, whose pages a CDN may keep; the setup page must not be kept, or opening the home
    after setup shows the cached Connected card again (found in a browser check, 13 Nov)."""
    from dataclasses import replace
    monkeypatch.setattr(server, "CFG", replace(server.CFG, demo=True))
    monkeypatch.setattr(server, "MODE", "demo")
    monkeypatch.delenv("DEMO", raising=False)
    monkeypatch.setattr(server.token_state, "saved", {"monitors": [{"name": "Bedroom", "kind": "indoor"}]})
    response = local.get("/", base_url=BASE)
    assert "data-wait-home" in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"


def test_setup_state_says_whether_the_app_still_waits_for_setup_and_is_never_cached(local, monkeypatch):
    """The Connected page asks this every few seconds; once setup restarts the app with the home, it says so."""
    from dataclasses import replace
    monkeypatch.setattr(server, "CFG", replace(server.CFG, demo=True))
    monkeypatch.setattr(server, "MODE", "demo")
    monkeypatch.delenv("DEMO", raising=False)
    response = local.get("/api/setup", base_url=BASE)
    assert response.get_json() == {"setup": True}
    assert response.headers["Cache-Control"] == "no-store"   # the demo's CDN rules never keep it
    monkeypatch.setattr(server, "MODE", "live")
    monkeypatch.setattr(server, "CFG", replace(server.CFG, demo=False))
    assert local.get("/api/setup", base_url=BASE).get_json() == {"setup": False}


def test_fresh_copy_card_lists_the_account_monitors(local, monkeypatch):
    from dataclasses import replace
    class Many(Reply):
        def json(self):
            return [{"locationId": i, "locationName": f"Room <{i}>"} for i in range(6)]
    monkeypatch.setattr(server, "CFG", replace(server.CFG, demo=True))
    monkeypatch.setattr(server, "MODE", "demo")
    monkeypatch.delenv("DEMO", raising=False)
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Many())
    assert submit(local).status_code == 303
    text = local.get("/", base_url=BASE).get_data(as_text=True)
    assert "6 monitors on your account" in text and "and 2 more" in text and "<li>Room <" not in text   # names are escaped


def test_upstream_debug_urls_cannot_log_candidate_token(local, monkeypatch, caplog):
    def debug_response(*a, **k):
        logging.getLogger("urllib3.connectionpool").debug('"GET /current?token=%s HTTP/1.1" 200', TOKEN)
        return Reply()
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(sources.requests, "get", debug_response)
    assert submit(local).status_code == 303
    assert TOKEN not in caplog.text and "token=***" in caplog.text


@pytest.mark.parametrize("status", [400, 401, 403])
def test_rejected_credentials_get_the_same_plain_error(local, monkeypatch, status):
    class Rejected(Reply):
        status_code = status
        def raise_for_status(self):
            raise requests.HTTPError("token=" + TOKEN)
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Rejected())
    response = submit(local)
    assert response.status_code == 400
    assert "AirGradient refused this token" in unescape(response.get_data(as_text=True))
    assert not server.env_path.exists()


def test_validation_makes_one_call_and_does_not_follow_redirects(local, monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs.get("allow_redirects") is False
        return Reply()
    monkeypatch.setattr(sources.requests, "get", get)
    assert submit(local).status_code == 303
    assert len(calls) == 1


def test_redirects_are_not_accepted_as_verified_credentials(local, monkeypatch):
    class Redirect(Reply):
        status_code = 302
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Redirect())
    assert submit(local).status_code == 502
    assert not server.env_path.exists()


@pytest.mark.parametrize("mode", ["no_token", "live", "demo"])
def test_all_app_pages_allow_same_origin_referrers(local, monkeypatch, mode):
    monkeypatch.setattr(server, "MODE", mode)
    assert page(local).headers["Referrer-Policy"] == "same-origin"


@pytest.mark.parametrize("headers", [{"Origin": BASE}, {"Origin": BASE, "Referer": BASE + "/"}])
def test_browser_same_origin_post_saves_token(local, headers):
    assert submit(local, headers=headers).status_code == 303


@pytest.mark.parametrize("token_set", [False, True])
@pytest.mark.parametrize("kwargs", [
    {"environ_overrides": {"REMOTE_ADDR": "192.168.1.8"}},
    {"headers": {"Origin": "http://evil.example"}},
    {"headers": {"Origin": "null"}},
    {"headers": {}},
])
def test_disallowed_callers_cannot_discover_token_state(local, monkeypatch, token_set, kwargs):
    value = nonce(page(local))
    if token_set:
        monkeypatch.setattr(server, "TOKEN", "already-set")
        monkeypatch.setattr(server, "MODE", "live")
    response = submit(local, value, **kwargs)
    assert response.status_code == 403
    assert response.get_json() == {"error": "Token entry is only available on this computer's local page."}


@pytest.mark.parametrize("cookie", ["é", "☃"])
def test_non_ascii_browser_cookie_is_refused(local, cookie):
    value = nonce(page(local))
    local.set_cookie(server.token_setup.COOKIE_NAME, cookie)
    assert submit(local, value).status_code == 403
    assert not server.env_path.exists()


@pytest.mark.parametrize("cookie", ["attacker-chosen", "é"])
def test_a_planted_cookie_is_replaced_not_adopted(local, cookie):
    local.set_cookie(server.token_setup.COOKIE_NAME, cookie)
    value = nonce(local.get("/connect", base_url=BASE))
    issued = local.get_cookie(server.token_setup.COOKIE_NAME).value
    assert issued != cookie
    local.set_cookie(server.token_setup.COOKIE_NAME, cookie)   # the browser sends the planted value with the form
    assert submit(local, value).status_code == 403
    assert not server.env_path.exists()


def test_a_cross_site_post_without_the_form_value_is_refused(local):
    """Another website can make the browser POST here (the Origin check also stops it) but cannot read the one-use value."""
    page(local)
    for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {}):
        assert local.post("/api/token", base_url=BASE, headers=headers, data={"token": TOKEN}).status_code == 403
    with server.app.test_client() as site:   # no cookie either: SameSite=Strict keeps it off cross-site requests
        assert site.post("/api/token", base_url=BASE, headers={"Origin": BASE}, data={"token": TOKEN}).status_code == 403
    assert not server.env_path.exists()


def test_validation_does_not_block_local_page_or_allow_a_second_save(local, monkeypatch):
    value = nonce(page(local))
    winner = "another-made-up-token"
    with ThreadPoolExecutor(max_workers=1) as pool:
        def validate(*a, **k):
            def another_browser():
                with server.app.test_client() as other:
                    login(other)
                    return other.post("/api/token", base_url=BASE,
                                      headers={"Origin": BASE, "Referer": BASE + "/"},
                                      data={"setup_nonce": nonce(page(other)), "token": winner}).status_code
            # A second browser can load the form and win the save during this validation.
            # Recursive validation is replaced only after this first call begins.
            monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Reply())
            assert pool.submit(another_browser).result(timeout=5) == 303
            return Reply()
        monkeypatch.setattr(sources.requests, "get", validate)
        assert submit(local, value).status_code == 303
    assert server.TOKEN == winner


def test_the_start_line_names_the_command_never_the_code_and_only_without_a_token(local, monkeypatch, capsys):
    server.print_setup_link(5600)   # this output goes to app.log: the code must not
    out = capsys.readouterr().out
    assert out == "No AirGradient token yet. For the private link to the token box, run: scripts/run.sh scripts/setup.py --connect\n"
    assert server.token_setup.owner_code(server.code_path) not in out
    monkeypatch.setattr(server, "TOKEN", TOKEN)
    server.print_setup_link(5600)
    assert capsys.readouterr().out == ""


def test_a_form_from_before_a_restart_is_refused(local, monkeypatch):
    value = nonce(page(local))
    monkeypatch.setattr(server, "token_state", server.token_setup.TokenState())
    assert submit(local, value).status_code == 403


def test_fresh_copy_can_collect_token_before_home_config(local, monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(server, "CFG", replace(server.CFG, demo=True))
    monkeypatch.setattr(server, "MODE", "demo")
    monkeypatch.delenv("DEMO", raising=False)
    assert submit(local).status_code == 303
    # Remain in the demo until setup writes a home and the agent restarts the app.
    assert server.MODE == "demo"
    # The page then says the token is saved and sends the user back to the agent, never the demo's made-up home.
    text = local.get("/", base_url=BASE).get_data(as_text=True)
    assert "Connected to AirGradient" in text and "1 monitor on your account" in text and "Unnamed monitor" in text and 'class="no-token fresh-copy"' in text and '"state": "no_token"' in text
    assert 'action="/api/token"' not in text and '"now":' not in text


def test_connected_card_names_each_monitor_indoor_or_outdoor_and_sends_the_user_back(local, monkeypatch):
    from dataclasses import replace
    class Two(Reply):
        def json(self):
            return [{"locationId": 1, "locationName": "Living room", "locationType": "indoor", "model": "I-9PSL"},
                    {"locationId": 2, "locationName": "Back porch", "model": "O-1PST"}]
    monkeypatch.setattr(server, "CFG", replace(server.CFG, demo=True))
    monkeypatch.setattr(server, "MODE", "demo")
    monkeypatch.delenv("DEMO", raising=False)
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Two())
    assert submit(local).status_code == 303
    text = unescape(local.get("/", base_url=BASE).get_data(as_text=True))
    assert "<b>Living room</b><em>Indoor monitor</em>" in text and "<b>Back porch</b><em>Outdoor monitor</em>" in text
    assert "<button" not in text.split("token-found", 1)[1].split("token-next", 1)[0]   # the monitors are a list, not controls
    # the agent waits for the token (setup.py --wait-token), so the user needn't type anything; "done" if it hasn't moved on.
    # Plain text on the page, the point first, no box (Sacha, 2026-11-13: "simplify this, be straight to the point ... not sure if a
    # container like this is useful")
    after = text.split("token-found", 1)[1]
    # the page switches to the home by itself once setup restarts the app (rehearsals 13 Nov: the tab still showed this card
    # until a reload), so it no longer says to close the tab
    # the point first: what happens, that nothing is needed, then the fallback; "done" only as the word to type (design check)
    # what to do first (Sacha, 13 Nov: "shouldnt we say to close the page? or do you mean that this page should stay open
    # and refreshes? i'm confused")
    assert ('<p class="token-next" data-wait-home><b>Keep this page open:</b> your home shows up here when your agent is done. '
            'If nothing happens, type “done” in your agent’s chat.</p>') in after
    assert "close this tab" not in after
    assert "go back to your agent" not in text and "says Connected" not in text and "<svg" not in after.split("token-next", 1)[1].split("</p>", 1)[0]


def test_a_malformed_token_is_told_apart_from_a_refused_one(local, monkeypatch):
    called = []
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: called.append(1) or Reply())
    response = local.post("/api/token", base_url=BASE, data={"setup_nonce": nonce(page(local)), "token": "has spaces in it"},
                          headers={"Origin": BASE, "Referer": BASE + "/"})
    assert response.status_code == 400 and not called   # never sent to AirGradient
    assert "doesn't look like an AirGradient token" in unescape(response.get_data(as_text=True))


def test_an_expired_form_is_a_page_with_a_way_forward_never_raw_json(local):
    page(local)
    response = submit(local, "a-form-value-from-an-old-tab")
    assert response.status_code == 403 and response.mimetype == "text/html"
    text = unescape(response.get_data(as_text=True))
    assert "nothing was sent" in text and 'name="setup_nonce"' in text   # a fresh form, ready for the token
    local.delete_cookie(server.token_setup.COOKIE_NAME)   # as after a restart: the old cookie no longer counts
    response = submit(local, "a-form-value-from-an-old-tab")
    assert response.status_code == 403 and response.mimetype == "text/html"
    assert "nothing was sent" in unescape(response.get_data(as_text=True)) and 'name="setup_nonce"' in response.get_data(as_text=True)



@pytest.fixture
def local_mode(local, monkeypatch):
    """A restarted copy that reads its monitors on the Wi-Fi and has no token: the box must still open."""
    monkeypatch.setattr(server, "MODE", "local")
    return local


def test_local_mode_opens_the_box_at_connect_only(local_mode):
    assert 'type="password"' in page(local_mode).get_data(as_text=True)
    assert 'type="password"' not in local_mode.get("/", base_url=BASE).get_data(as_text=True)   # the home stays the home
    away = page(local_mode, environ_overrides={"REMOTE_ADDR": "192.168.1.8"}).get_data(as_text=True)
    assert 'type="password"' not in away and "computer it runs on" in away


def test_local_mode_saves_the_token_and_goes_live_then_the_box_closes(local_mode):
    assert submit(local_mode).status_code == 303
    assert server.MODE == "live" and server.env_path.read_text() == f"AIRGRADIENT_TOKEN={TOKEN}\n"
    response = local_mode.get("/connect", base_url=BASE)
    assert response.status_code == 303 and response.headers["Location"] == "/"
    assert server.env_path.read_text() == f"AIRGRADIENT_TOKEN={TOKEN}\n"


def test_setup_opens_the_private_link_and_it_leads_home_then_to_the_box(local_mode, monkeypatch, capsys):
    """Setup opens the page of a home read on the Wi-Fi with no token. A bare address never gives that browser the
    owner cookie, so the box would only ever say "open the private link". The link setup opens carries the code, shows
    the home first, and leaves the box open at /connect."""
    import subprocess
    import sys
    sys.path.insert(0, str(Path(server.__file__).parent / "scripts"))
    import setup
    root = server.code_path.parent   # setup's folder: the same connect-code file the app reads
    (root / setup.PORT_FILE).write_text("5555\n")
    for step in ("find_home", "start_app", "start_at_login", "agent_skill", "live"):
        monkeypatch.setattr(setup, step, lambda *a, **k: 0)
    monkeypatch.setattr(setup, "known_serials", lambda root: ["aabbccddeeff"])
    monkeypatch.setattr(setup.localnet, "environment_blocker", lambda *a, **k: None)
    opened = []
    monkeypatch.setattr(setup.subprocess, "run", lambda cmd, **k: opened.append(cmd[-1]) or subprocess.CompletedProcess(cmd, 0))
    assert setup.one_run({}, root) == 0
    code = server.token_setup.owner_code(server.code_path, create=False)
    assert opened == [f"{BASE}/connect?code={code}"] and code not in capsys.readouterr().out
    with server.app.test_client() as browser:   # a browser that has never had the owner cookie
        assert 'type="password"' not in browser.get("/connect", base_url=BASE).get_data(as_text=True)
        response = browser.get(opened[0].removeprefix(BASE), base_url=BASE)
        assert '<meta http-equiv="refresh" content="0; url=/">' in response.get_data(as_text=True)
        assert 'type="password"' not in browser.get("/", base_url=BASE).get_data(as_text=True)   # the home first
        assert 'type="password"' in browser.get("/connect", base_url=BASE).get_data(as_text=True)


def test_a_saved_token_closes_the_box_before_the_app_notices(local_mode):
    value = nonce(page(local_mode))
    server.env_path.write_text("AIRGRADIENT_TOKEN=typed-in-a-terminal\n")
    assert 'type="password"' not in page(local_mode).get_data(as_text=True)
    assert submit(local_mode, value).status_code == 303
    assert server.env_path.read_text() == "AIRGRADIENT_TOKEN=typed-in-a-terminal\n"


# The owner's private link (security review of babb53b, 22 Nov): the box answered any program on this computer, another
# user of it, or a tunnel to 127.0.0.1. Each test below guards one of those holes.

OWNER = server.token_setup.OWNER_COOKIE
FORM = server.token_setup.COOKIE_NAME


def no_network(monkeypatch):
    calls = []
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: calls.append(1) or Reply())
    return calls


@pytest.mark.parametrize("headers", [{}, {"Origin": BASE, "Referer": BASE + "/", "Host": "localhost:5555"},
                                     {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate"}])
def test_a_local_program_without_the_owner_cookie_gets_no_box_and_cannot_save(local, monkeypatch, headers):
    """A program on this computer reaches the loopback address and can forge Origin and Host, but holds no owner cookie:
    no box, no form value, and its save stops at the owner check, before the token is even looked at."""
    calls = no_network(monkeypatch)
    owner_value = nonce(page(local))   # the owner's open form
    with server.app.test_client() as program:
        response = program.get("/connect", base_url=BASE, headers=headers)
        text = unescape(response.get_data(as_text=True))
        assert 'type="password"' not in text and 'name="setup_nonce"' not in text
        assert "open the private link setup printed, or run <code>scripts/run.sh scripts/setup.py --connect</code>" in text
        assert OWNER not in response.headers.get("Set-Cookie", "") and FORM not in response.headers.get("Set-Cookie", "")
        for value in ("forged", owner_value):
            response = program.post("/api/token", base_url=BASE, headers={"Origin": BASE, "Referer": BASE + "/", **headers},
                                    data={"setup_nonce": value, "token": "has spaces so the format check would refuse it"})
            assert response.status_code == 403 and response.get_json() == {"error": server.OWNER_NEEDED_ERROR}
    assert not calls and not server.env_path.exists()
    assert submit(local, owner_value).status_code == 303   # the owner's form still works


def test_a_wrong_code_sets_nothing_and_compares_in_constant_time(local, monkeypatch):
    code = server.token_setup.owner_code(server.code_path)
    with server.app.test_client() as program:
        for wrong in ("", "x", code[:-1], code + "x", code[:-1] + ("A" if code[-1] != "A" else "B"), "é" * 50, "x" * 5000):
            response = program.get("/connect", base_url=BASE, query_string={"code": wrong})
            assert response.status_code == 403 and "Set-Cookie" not in response.headers
            text = unescape(response.get_data(as_text=True))
            assert 'type="password"' not in text and "isn't the private one setup printed" in text and code not in text
    seen = []
    real = server.token_setup.hmac.compare_digest
    monkeypatch.setattr(server.token_setup.hmac, "compare_digest", lambda a, b: seen.append((len(a), len(b))) or real(a, b))
    assert not server.token_setup.code_matches("short", code) and server.token_setup.code_matches(code, code)
    assert seen == [(64, 64), (64, 64)]   # both sides hashed first: the time doesn't depend on how much of it matched


def test_the_right_code_sets_the_owner_cookie_and_reloads_without_the_code(monkeypatch, local):
    code = server.token_setup.owner_code(server.code_path)
    with server.app.test_client() as browser:
        response = browser.get("/connect?code=" + code, base_url=BASE)
        assert response.status_code == 200
        cookie = response.headers["Set-Cookie"]
        assert cookie.startswith(OWNER + "=") and code not in cookie
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/;" in cookie + ";" and "Max-Age=31536000" in cookie
        body = response.get_data(as_text=True)
        assert '<meta http-equiv="refresh" content="0; url=/connect">' in body and code not in body
        assert response.headers["Referrer-Policy"] == "no-referrer" and response.headers["Cache-Control"] == "no-store"
        assert "Location" not in response.headers
        assert 'type="password"' in browser.get("/connect", base_url=BASE).get_data(as_text=True)
        assert 'type="password"' in browser.get("/", base_url=BASE).get_data(as_text=True)   # no_token: the page is the box


@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-IP", "CF-Connecting-IP",
                                    "Tailscale-User-Login", "Tailscale-User-Name"])
def test_forwarded_requests_are_refused(local, monkeypatch, header):
    """tailscale serve, cloudflared, ngrok or ssh -R reach the app from 127.0.0.1; the headers they add give them away."""
    calls = no_network(monkeypatch)
    value = nonce(page(local))
    code = server.token_setup.owner_code(server.code_path)
    forwarded = {header: "203.0.113.9"}
    response = local.get("/connect", base_url=BASE, headers=forwarded)
    assert response.status_code == 403 and response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == server.FORWARDED_ERROR + "\n"
    assert local.get("/connect?code=" + code, base_url=BASE, headers=forwarded).status_code == 403
    assert 'type="password"' not in local.get("/", base_url=BASE, headers=forwarded).get_data(as_text=True)
    assert submit(local, value, headers={"Origin": BASE, **forwarded}).status_code == 403
    assert not calls and not server.env_path.exists()


def test_cookieless_and_cross_site_requests_never_evict_a_real_form(local):
    """Any website can make a browser load this page; it never had the owner cookie (Strict), so it never fills the form
    store, and a full store refuses new forms instead of dropping old ones."""
    value = nonce(page(local))
    with server.app.test_client() as site:
        for _ in range(server.token_setup.NONCE_LIMIT + 8):
            site.get("/connect", base_url=BASE, headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"})
            site.get("/", base_url=BASE)
    # the owner's own browser asks from another site's script (no navigation): no form either
    text = local.get("/connect", base_url=BASE, headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}).get_data(as_text=True)
    assert 'name="setup_nonce"' not in text
    assert len(server.token_state.nonces) == 1
    # the owner's other browsers fill the store: the next one is told so, and the first form still works
    others = []
    for _ in range(server.token_setup.NONCE_LIMIT - 1):
        other = server.app.test_client()
        login(other)
        nonce(page(other))
        others.append(other)
    late = server.app.test_client()
    login(late)
    assert "Too many token forms are open" in page(late).get_data(as_text=True)
    for _ in range(3):   # a browser that already has a form makes room among its own
        nonce(page(local))
    assert submit(local).status_code == 303


@pytest.mark.parametrize("name", [FORM, OWNER])
def test_a_planted_extra_cookie_does_not_block_a_real_save_and_is_cleared(local, name):
    """Another app on localhost (cookies don't keep ports apart) plants a cookie on the form's path, which the browser
    sends first. Every value is checked, the stray one is cleared, and the owner's save goes through."""
    value = nonce(page(local))
    local.set_cookie(name, "planted-by-another-app", path="/api/token")
    response = submit(local, value)
    assert response.status_code == 303 and server.env_path.read_text() == f"AIRGRADIENT_TOKEN={TOKEN}\n"
    cleared = [h for h in response.headers.getlist("Set-Cookie") if h.startswith(name + "=") and "Path=/api/token" in h]
    assert cleared and ("Max-Age=0" in cleared[0] or "Expires=Thu, 01 Jan 1970" in cleared[0])


def test_a_bad_owner_cookie_alone_is_cleared_at_both_paths(local):
    with server.app.test_client() as browser:
        browser.set_cookie(OWNER, "guessed")
        response = browser.get("/connect", base_url=BASE)
        assert 'type="password"' not in response.get_data(as_text=True)
        paths = sorted(h.split("Path=")[1].split(";")[0] for h in response.headers.getlist("Set-Cookie") if h.startswith(OWNER + "="))
        assert paths == ["/", "/api/token"]


def test_failures_from_others_never_lock_the_owner_out(local, monkeypatch):
    """Before, five bad tokens from anyone closed the box for five minutes; only the owner cookie reaches the count now."""
    with server.app.test_client() as program:
        for _ in range(10):
            assert program.post("/api/token", base_url=BASE, headers={"Origin": BASE}, data={"token": "bad token"}).status_code == 403
    assert not server.token_state.failures
    assert submit(local).status_code == 303


def test_the_app_never_makes_a_code_itself(local, monkeypatch, tmp_path):
    """Only setup prints the link, so only setup writes the code: a page view or a guessed link never creates one."""
    monkeypatch.setattr(server, "code_path", tmp_path / "app" / "none-yet")
    with server.app.test_client() as browser:
        assert browser.get("/connect?code=anything", base_url=BASE).status_code == 403
        assert 'type="password"' not in browser.get("/connect", base_url=BASE).get_data(as_text=True)
    assert not server.code_path.exists()


def test_the_code_file_is_private_and_kept_across_restarts(local, monkeypatch, tmp_path):
    path = server.code_path
    code = server.token_setup.owner_code(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(code) >= 43   # 256 bits, urlsafe
    monkeypatch.setattr(server, "token_state", server.token_setup.TokenState())   # a restart: forms go, the code stays
    assert server.token_setup.owner_code(path) == code
    assert 'type="password"' in page(local).get_data(as_text=True)   # the owner cookie from before still opens the box
    path.chmod(0o644)   # loosened by hand: tightened again, same code
    assert server.token_setup.owner_code(path) == code and stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("short\n")   # damaged: the app reads no code (box closed); setup's next link writes a new one
    assert server.token_setup.owner_code(path, create=False) is None
    assert 'type="password"' not in page(local).get_data(as_text=True)
    fresh = server.token_setup.owner_code(path)
    assert fresh != code and server.token_setup.CODE_FORMAT.fullmatch(fresh)
    assert 'type="password"' not in page(local).get_data(as_text=True)
    other = tmp_path / "elsewhere"
    other.symlink_to(path)
    with pytest.raises(OSError):
        server.token_setup.owner_code(other)   # never read through a link
    assert sorted(p.name for p in path.parent.iterdir()) == [server.token_setup.CODE_FILE]   # no temp file left


def test_the_code_never_reaches_the_request_log(local, caplog):
    code = server.token_setup.owner_code(server.code_path)
    caplog.set_level(logging.INFO, logger="werkzeug")
    logging.getLogger("werkzeug").info('%s - - [%s] "%s" %s %s', "127.0.0.1", "22/Nov/2026 10:00:00",
                                       f"GET /connect?code={code} HTTP/1.1", "200", "-")
    logging.getLogger("werkzeug").info('"%s" %s %s', f"\x1b[1mGET /connect?other=1&code={code} HTTP/1.1\x1b[0m", "403", "-")
    assert code not in caplog.text and caplog.text.count("code=***") == 2   # (pytest strips the colors from caplog.text)


# --- A token added later (Task 17): the home's own config.toml, written by the app. Real requests go to the fake cloud
# (scripts/fake_airgradient.py) on loopback; the config, its backups and the history store live in tmp_path.

HOME_SERIALS = {"indoor": "aabbccddeeff", "outdoor": "112233445566"}
HOME_TOML = """# Our home: hand notes stay where they are
[place]
timezone = "America/New_York"
indoor = "bedroom"   # renamed by us
indoor_serial = "aabbccddeeff"

# the balcony one
outdoor = "outdoor"
outdoor_serial = "112233445566"

[profiles]
toddler = false
"""


@pytest.fixture
def later(local, monkeypatch, tmp_path):
    """A home whose config is a file, the fake cloud on loopback, history fills run inline. Gives the client, a helper that
    writes the config and switches the server to it, the fake cloud, and the cloud paths asked (without the token)."""
    import threading
    from config import load_config
    from scripts import fake_airgradient as fake
    cloud = fake.make_server(0)
    threading.Thread(target=cloud.serve_forever, daemon=True).start()
    monkeypatch.setattr(sources, "API_BASE", f"http://127.0.0.1:{cloud.server_port}{fake.API_PATH.removesuffix('/locations')}")
    asked = []

    def get(url, **kwargs):
        asked.append(url.split("?")[0].removeprefix(sources.API_BASE))
        return REAL_GET(url, **kwargs)
    monkeypatch.setattr(sources.requests, "get", get)
    pool = sources._REFRESH_POOL   # fills run inline, so a test sees them done; the cache's refreshes stay in the pool

    class FillsInline:
        def submit(self, fn, *args):
            return fn(*args) if fn is sources._fill_one else pool.submit(fn, *args)
    monkeypatch.setattr(sources, "_REFRESH_POOL", FillsInline())
    monkeypatch.setattr(sources, "_asked_until", {})
    monkeypatch.setattr(sources, "_MONITOR_VIA", {})
    before = server.CFG
    path = tmp_path / "cfg" / "config.toml"
    path.parent.mkdir()

    def home(text, mode):
        path.write_text(text, encoding="utf-8")
        monkeypatch.delenv("CONFIG_TOML", raising=False)
        monkeypatch.setenv("CONFIG_FILE", str(path))
        server.use_config(load_config())
        monkeypatch.setattr(server, "MODE", mode)
        monkeypatch.setattr(server, "POLLER", None)
        monkeypatch.setattr(server, "STORE", None)
        return path
    yield local, home, cloud, asked
    cloud.shutdown()
    cloud.server_close()
    server.use_config(before)
    sources._cache.clear()


def wifi_home(monkeypatch, tmp_path, home):
    """HOME_TOML read on the Wi-Fi, no token: a poller that has read both monitors once, and a store holding a reading of
    each from two days ago, so the week has gaps to fill."""
    from datetime import datetime, timedelta, timezone
    import localnet
    from history_store import HistoryStore
    path = home(HOME_TOML, "local")
    payload = {"pm02": 5.3, "pm02Compensated": 4.2, "rco2": 612, "atmp": 21.4, "rhum": 40.1, "firmware": "3.7.1"}
    memory = type("Memory", (), {"get": lambda self, serial: ("127.0.0.1", 80), "remember": lambda self, serial, address, port=80: None})()
    poller = localnet.Poller(server.config_monitors(server.CFG), browse=False, memory=memory,
                             read=lambda mon, address, port: {**payload, "serialno": mon.serial, "model": "I-9PSL"})
    poller.tick()
    store = HistoryStore(tmp_path / "history.sqlite")
    then = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for serial in HOME_SERIALS.values():
        store.add(serial, {"timestamp": then, "rco2": 500, "atmp": 21.0})
    monkeypatch.setattr(server, "POLLER", poller)
    monkeypatch.setattr(server, "STORE", store)
    return path, poller, store, then


def test_token_matches_by_serial_and_keeps_a_renamed_monitor(later, monkeypatch, tmp_path):
    """check 12 and Review Focus 2: the household's own config.toml is rewritten by the app, so it is backed up first and
    every hand line stays; monitors match by serial, whatever its case and whatever the names say (an account monitor
    named like ours, with another serial, is not taken); the poller keeps its readings under the new ids; the week fills
    with one /measures/past call per monitor and the stored local rows stay as they were."""
    from datetime import datetime, timedelta, timezone
    from scripts import fake_airgradient as fake
    client, home, cloud, asked = later
    path, poller, store, then = wifi_home(monkeypatch, tmp_path, home)
    cloud.monitors = [
        {"locationId": 7001, "locationName": "bedroom", "locationType": "indoor", "serialno": "999999999999", "pm02": 9},
        {"locationId": 7003, "locationName": "Kids room", "locationType": "indoor", "serialno": "AABBCCDDEEFF", "pm02": 3},
        {"locationId": 7002, "locationName": "Balcony", "locationType": "outdoor", "serialno": "112233445566", "pm02": 5},
    ]
    assert submit(client, token=fake.TEST_TOKEN).status_code == 303
    assert path.read_text(encoding="utf-8") == HOME_TOML.replace(
        'indoor = "bedroom"   # renamed by us\n', 'indoor = "bedroom"   # renamed by us\nindoor_id = 7003\n').replace(
        'outdoor = "outdoor"\n', 'outdoor = "Balcony"\noutdoor_id = 7002\n')
    backups = list(path.parent.glob("config.toml.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == HOME_TOML
    assert server.MODE == "live" and server.CFG.place.indoor == "bedroom" and server.CFG.place.indoor_id == 7003
    assert server.POLLER is poller and poller.state("indoor") == "local"
    assert (poller.latest("indoor")["locationId"], poller.latest("outdoor")["locationName"]) == (7003, "Balcony")
    past = sorted(p for p in asked if p.endswith("/measures/past"))
    assert past == ["/locations/7002/measures/past", "/locations/7003/measures/past"]
    now = datetime.now(timezone.utc)
    for serial in HOME_SERIALS.values():
        rows = store.rows(serial, now - timedelta(days=7, minutes=10), now)
        assert len(rows) > 100   # the week, from the cloud
        assert [r["rco2"] for r in rows if r["timestamp"] == then] == [500]   # ours, unchanged



def test_a_monitor_the_account_does_not_have_is_not_kept_as_the_homes(later, monkeypatch, tmp_path):
    """On a shared Wi-Fi the monitor setup found can be a neighbour's: once a token says whose monitors are the home's,
    the account's monitor of that kind replaces it (serial and id, so it is read on the Wi-Fi), and a cloud id never
    stays beside a serial that isn't its monitor's (the page and the fill would mix two monitors' readings)."""
    import tomllib
    from scripts import fake_airgradient as fake
    client, home, cloud, asked = later
    path, poller, store, then = wifi_home(monkeypatch, tmp_path, home)
    path.write_text(HOME_TOML.replace('indoor_serial = "aabbccddeeff"\n', 'indoor_serial = "aabbccddeeff"\nindoor_id = 7009\n'))
    server.use_config(server.load_config())
    ours = {"locationId": 7003, "locationName": "Kids room", "locationType": "indoor", "serialno": "0A0B0C0D0E0F", "pm02": 3}
    balcony = {"locationId": 7002, "locationName": "Balcony", "locationType": "outdoor", "serialno": "112233445566", "pm02": 5}
    cloud.monitors = [ours, balcony]
    assert submit(client, token=fake.TEST_TOKEN).status_code == 303
    place = tomllib.loads(path.read_text(encoding="utf-8"))["place"]
    assert (place["indoor_serial"], place["indoor_id"], place["indoor"]) == ("0a0b0c0d0e0f", 7003, "bedroom")
    assert (place["outdoor_serial"], place["outdoor_id"]) == ("112233445566", 7002)
    assert server.POLLER.latest("indoor") is None   # the neighbour's reading is no longer the home's
    # no indoor monitor in the account: the one found stays read on the Wi-Fi, without the other monitor's id
    assert sources.match_account(server.load_config({"CONFIG_TOML": HOME_TOML.replace(
        'indoor_serial = "aabbccddeeff"\n', 'indoor_serial = "aabbccddeeff"\nindoor_id = 7009\n')}), [balcony]) == {
        "indoor": {"indoor_id": None}, "outdoor": {"outdoor_id": 7002, "outdoor": "Balcony"}}


def test_a_token_typed_into_the_keys_file_is_matched_at_start(later, monkeypatch, tmp_path):
    """A token added to .env by hand (then a restart) never goes through the box: the monitors would keep their local
    ids, so no fill and no cloud fallback. At start the app matches them as a saved token does, once, and the filled
    rows keep the temperature the summary reads (atmp), not only the charts' fields."""
    from datetime import datetime, timedelta, timezone
    from scripts import fake_airgradient as fake
    client, home, cloud, asked = later
    path, poller, store, then = wifi_home(monkeypatch, tmp_path, home)
    cloud.monitors = [{"locationId": 7003, "locationName": "Kids room", "locationType": "indoor", "serialno": "aabbccddeeff", "atmp": 20.5, "pm02": 3},
                      {"locationId": 7002, "locationName": "Balcony", "locationType": "outdoor", "serialno": "112233445566", "atmp": 9.5, "pm02": 5}]
    monkeypatch.setattr(server, "TOKEN", fake.TEST_TOKEN)   # read from .env at start, as server.py does
    monkeypatch.setattr(server, "MODE", "live")
    server.adopt_saved_token(start=lambda fn: fn())
    assert server.CFG.place.indoor_id == 7003 and server.CFG.place.outdoor_id == 7002
    assert "indoor_id = 7003\n" in path.read_text(encoding="utf-8") and len(list(path.parent.glob("config.toml.bak-*"))) == 1
    assert sorted(p for p in asked if p.endswith("/measures/past")) == ["/locations/7002/measures/past", "/locations/7003/measures/past"]
    now = datetime.now(timezone.utc)
    filled = [r for r in store.rows(HOME_SERIALS["indoor"], now - timedelta(days=7), now) if r["timestamp"] != then]
    assert filled and {r.get("atmp") for r in filled} == {20.5}
    asked.clear()
    server.adopt_saved_token(start=lambda fn: fn())   # matched now: the next start asks nothing
    assert asked == []


def test_refused_token_changes_nothing(later, monkeypatch, tmp_path):
    """check 13: a token the cloud refuses writes neither file, makes no backup and asks for no history."""
    client, home, cloud, asked = later
    path, poller, store, _ = wifi_home(monkeypatch, tmp_path, home)
    server.env_path.write_text("OTHER=keep\n")
    response = submit(client, token="not-the-fake-clouds-token")
    assert response.status_code == 400 and "refused" in unescape(response.get_data(as_text=True))
    assert path.read_text(encoding="utf-8") == HOME_TOML and sorted(p.name for p in path.parent.iterdir()) == ["config.toml"]
    assert server.env_path.read_text() == "OTHER=keep\n"
    assert asked == ["/locations/measures/current"] and server.MODE == "local" and server.CFG.place.indoor_id is None


@pytest.mark.parametrize("account,place", [(None, ""), ("indoor", 'name = "Greenpoint"\nlat = 40.73\nlon = -73.95\n')])
def test_nothing_found_then_token_shows_cloud_rows(later, account, place):
    """check 15 and spec path 2: setup found no monitor, so config.toml names none; after the token the account's
    monitors (today's rule, one per kind) are written and read from the cloud, with no restart. An indoor-only account
    with a place already set also needs an outdoor source, or the file wouldn't load: the area's estimate."""
    from scripts import fake_airgradient as fake
    client, home, cloud, asked = later
    path = home(f'[place]\n{place}timezone = "America/New_York"\n\n[profiles]\nrunner = false\n', "no_token")
    if account:
        cloud.monitors = [m for m in cloud.monitors if m["locationType"] == account]
    assert submit(client, token=fake.TEST_TOKEN).status_code == 303
    assert server.MODE == "live" and server.POLLER is None and server.STORE is None
    text = path.read_text(encoding="utf-8")
    assert 'indoor = "Bedroom"\nindoor_id = 7001\n' in text and text.endswith("[profiles]\nrunner = false\n" + ('\n[sources]\noutdoor_from = "area"\n' if account else ""))
    if account:
        return   # its outdoor source reads the public map: not this test's
    assert 'outdoor = "Balcony"\noutdoor_id = 7002\n' in text
    rows = client.get("/api/current").get_json()
    assert [(r["locationId"], r["locationType"], r.get("via", "cloud")) for r in rows] == [(7001, "indoor", "cloud"), (7002, "outdoor", "cloud")]
    assert client.get("/health").get_json()["monitors"] == {"indoor": "cloud", "outdoor": "cloud"}
    assert not [p for p in asked if p.endswith("/measures/past")]   # no store here: the charts ask the cloud, as before
