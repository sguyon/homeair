"""Local first-run entry: request guards, the owner's private code and short-lived, browser-bound form values."""
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from config import env_key, parse_dotenv, real_value

# Hosting environment markers: any one (even empty) means keys belong in the host's settings.
HOSTED_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "VERCEL", "RENDER", "FLY_APP_NAME", "K_SERVICE", "DYNO",
                  "KUBERNETES_SERVICE_HOST", "KOYEB_APP_NAME", "WEBSITE_INSTANCE_ID")
DOCKER_MARKER = Path("/.dockerenv")  # Docker creates this file inside its containers.
PODMAN_MARKER = Path("/run/.containerenv")  # Podman creates this file inside its containers.
LOCAL_ADDRESSES = ("127.0.0.1", "::1")  # Only the socket peer's loopback address grants local entry.
LOCAL_NAMES = ("localhost", "127.0.0.1", "[::1]")  # The only names the token box answers to; any other name could be a web page's rebound one.
DEFAULT_PORT = 5555  # the app's port when PORT isn't set (server.py's own default).
BROWSER_BYTES = 32  # random bytes for the cookie that ties one-use form values to the browser that loaded them.
BROWSER_LIMIT = 32  # browsers remembered at once: bounds memory.
TOKEN_CHARACTERS = re.compile(r"[A-Za-z0-9._~+=/-]+")  # Plain API credentials; excludes whitespace and dotenv quotes.
TOKEN_MAX_LENGTH = 4096  # characters: enough for an API credential, while bounding malformed input.
TOKEN_POST_MAX_BYTES = 8192  # bytes: bounds the token plus form encoding and the one-use value.
FAILED_ATTEMPT_LIMIT = 5  # failures per window. Only the owner cookie reaches the count, and it has one value: one budget, the owner's.
FAILED_ATTEMPT_WINDOW = 300  # seconds: retry after five minutes rather than locking out a new setup.
NONCE_LIFETIME = 600  # seconds: a form expires after ten minutes; reload makes another.
NONCE_LIMIT = 32  # outstanding forms: bounds memory even if pages are repeatedly opened.
NONCE_BYTES = 32  # random bytes for one-use form values (256 bits).
COOKIE_NAME = "aq_token_setup"  # HttpOnly browser cookie the page sets itself; never contains the AirGradient token.
COOKIE_PATHS = ("/", "/api/token")  # where a cookie is cleared: ours live at /, a planted one may sit on the form's path.
# The owner's code: a random value in this file next to .env (mode 0600, gitignored), kept across restarts. Only the link
# setup prints (/connect?code=...) carries it; the box and the save also need the owner cookie that link sets.
CODE_FILE = "connect-code"
CODE_BYTES = 32  # random bytes in the code (256 bits).
CODE_FORMAT = re.compile(r"[A-Za-z0-9_-]{43,}")  # what token_urlsafe(32) writes; anything else in the file is replaced.
CODE_FILE_MODE = 0o600  # owner read and write only: another user of this computer can't read the code.
OWNER_COOKIE = "aq_owner"  # HttpOnly, SameSite=Strict, Path=/: a value derived from the code, never the code itself.
OWNER_COOKIE_AGE = 365 * 24 * 3600  # seconds: a year, so the owner logs in once per browser.
OWNER_LABEL = b"home-air token box owner v1"  # the HMAC message that turns the code into the owner cookie's value.
# Headers a proxy or tunnel adds (tailscale serve, cloudflared, ngrok, nginx): the box never opens through one.
FORWARD_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-IP", "CF-Connecting-IP",
                   "Tailscale-User-Login", "Tailscale-User-Name")
FORM_SITES = ("none", "same-origin")  # Sec-Fetch-Site values that may get a form: typed or opened by this page itself.


def forwarded(req):
    """A proxy or tunnel forwarded this request to the loopback address: the peer isn't really this computer."""
    return any(name in req.headers for name in FORWARD_HEADERS)


def local_request(req):
    """Use the socket peer's address, never a header."""
    return req.remote_addr in LOCAL_ADDRESSES


def app_port(env=os.environ):
    try:
        return int(env.get("PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def local_host(req, port=None):
    """The address typed is localhost, 127.0.0.1 or [::1] with the app's port: a web page that rebinds its own name to
    this computer reaches the app under that name, so it fails here."""
    name, sep, typed = req.headers.get("Host", "").lower().rpartition(":")
    if not sep or name not in LOCAL_NAMES:
        return False
    return typed == str(app_port() if port is None else port)


def hosted(env=os.environ):
    return any(marker in env for marker in HOSTED_MARKERS)


def has_token(path, token):
    """Check both sources again, including a file changed since startup; never return its value."""
    if real_value(token) or env_key("AIRGRADIENT_TOKEN"):
        return True
    try:
        return bool(env_key("AIRGRADIENT_TOKEN", parse_dotenv(path.read_text(encoding="utf-8"))))
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError):
        return True  # an unreadable file cannot safely be treated as empty


def writable_folder(path):
    """Atomic replacement needs write and search access to the parent. Browser setup won't follow symlinks."""
    try:
        return (not path.is_symlink() and bool(path.parent.stat().st_mode & 0o222)
                and os.access(path.parent, os.W_OK | os.X_OK))
    except OSError:
        return False


def local_entry(req, path):
    """Check the local environment before revealing whether a token already exists."""
    return (local_request(req) and not forwarded(req) and local_host(req) and not hosted() and not DOCKER_MARKER.exists()
            and not PODMAN_MARKER.exists() and writable_folder(path))


def allowed(req, path, token, mode):
    return mode in ("local", "no_token") and local_entry(req, path) and not has_token(path, token)


def instructions(req):
    """Hosting instructions take precedence over the address used to reach a deployed copy."""
    if "RAILWAY_ENVIRONMENT" in os.environ or "RAILWAY_PROJECT_ID" in os.environ:
        return "railway"
    if DOCKER_MARKER.exists() or PODMAN_MARKER.exists():
        return "docker"
    if not hosted() and not (local_request(req) and not forwarded(req) and local_host(req)):
        return "network"
    return "file"


def origin(req):
    return f"{req.scheme}://{req.host.lower()}"


def same_origin(req):
    """Require Origin or Referer. If both are present, both must match, including scheme and port."""
    expected = origin(req)
    values = []
    for name in ("Origin", "Referer"):
        if name not in req.headers:
            continue
        value = req.headers[name]
        try:
            parsed = urlsplit(value)
            if parsed.username is not None or parsed.password is not None or parsed.fragment:
                return False
            if name == "Origin" and (parsed.path or parsed.query):
                return False
            values.append(f"{parsed.scheme}://{parsed.netloc.lower()}" == expected)
        except ValueError:
            return False
    return bool(values) and all(values)


def _write_code(path, replace):
    """A new code in a 0600 file written aside, then put in place: a reader never sees a half-written file. Without
    replace, a code another process wrote first is kept."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, CODE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secrets.token_urlsafe(CODE_BYTES) + "\n")
        if replace:
            os.replace(tmp, path)
        else:
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
    finally:
        tmp.unlink(missing_ok=True)


def owner_code(path, create=True):
    """The owner's code from `path`; one others can read is made 0600 first. setup.py (create) writes a new one when the
    file is missing, damaged or not this user's; the app only reads it (None then), so it never makes one nobody printed.
    The app never prints or logs it; setup.py prints the link that carries it."""
    path = Path(path)
    for _ in range(3):
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            if not create:
                return None
            _write_code(path, replace=False)
            continue
        with os.fdopen(fd, encoding="ascii", errors="replace") as f:
            info = os.fstat(f.fileno())
            mine = info.st_uid == os.getuid()   # a file someone else put here is replaced, never trusted
            if mine and info.st_mode & 0o077:
                os.fchmod(f.fileno(), CODE_FILE_MODE)
            code = f.read(256).strip()
        if mine and CODE_FORMAT.fullmatch(code):
            return code
        if not create:
            return None
        _write_code(path, replace=True)
    raise OSError(f"couldn't keep a code in {path.name}")


def owner_value(code):
    """The owner cookie's value: an HMAC of the code, so the cookie never holds the code itself."""
    return hmac.new(code.encode("utf-8", "replace"), OWNER_LABEL, hashlib.sha256).hexdigest()


def code_matches(given, code):
    """Constant time whatever the given value's length or content: both sides are hashed to the same length first."""
    return hmac.compare_digest(owner_value(given), owner_value(code))


def owner_cookie(req, code):
    """(valid, values): whether any owner cookie the browser sent is this code's, and every value it sent."""
    values = req.cookies.getlist(OWNER_COOKIE)
    expected = owner_value(code).encode()
    return any(hmac.compare_digest(v.encode("utf-8", "replace"), expected) for v in values), values


def form_request(req):
    """A browser says where a request comes from (Sec-Fetch-*): only one typed or opened by this page, or a top-level
    navigation, may take a form. Without those headers (not a browser) the owner cookie is the gate."""
    site, mode = req.headers.get("Sec-Fetch-Site"), req.headers.get("Sec-Fetch-Mode")
    if site is None and mode is None:
        return True
    return site in FORM_SITES or mode == "navigate"


class _CodeURLFilter(logging.Filter):
    """The dev server logs each request line: hide the code of a /connect?code= link before any handler sees it."""
    def filter(self, record):
        message = record.getMessage()
        if "code=" in message:
            record.msg, record.args = re.sub(r"([?&]code=)[^&\s\"'\x1b]*", r"\1***", message), ()
        return True


logging.getLogger("werkzeug").addFilter(_CodeURLFilter())


class TokenState:
    """Single local process: serialize saves, consume forms once and share the failed-attempt budget."""

    def __init__(self):
        self.saved = None   # {"monitors": [{"name", "kind"}]} once a token was saved from the page; a fresh copy says so until it restarts
        self.lock = threading.RLock()
        self.browsers = {}  # browser cookie value -> expiry; only values this server issued count
        self.nonces = {}
        self.failures = deque()

    def issue(self, req):
        """A one-use form value tied to this browser's cookie (a new cookie unless it sent one issued here), or
        (None, None) when the store is full: old forms of other browsers are never dropped to make room."""
        with self.lock:
            now = time.monotonic()
            self.browsers = {key: expiry for key, expiry in self.browsers.items() if expiry > now}
            self.nonces = {key: value for key, value in self.nonces.items() if value[2] > now}
            # never adopt a value the browser brought: it could be planted; check each one it sent
            browser = next((c for c in req.cookies.getlist(COOKIE_NAME) if c in self.browsers), None)
            if browser is None:
                if len(self.browsers) >= BROWSER_LIMIT:
                    return None, None
                browser = secrets.token_urlsafe(BROWSER_BYTES)
            if len(self.nonces) >= NONCE_LIMIT:
                own = next((key for key, value in self.nonces.items() if value[0] == browser), None)
                if own is None:
                    return None, None
                self.nonces.pop(own)   # this browser's oldest form makes room for its newest
            self.browsers[browser] = now + NONCE_LIFETIME
            value = secrets.token_urlsafe(NONCE_BYTES)
            self.nonces[value] = (browser, origin(req), now + NONCE_LIFETIME)
            return value, browser

    def known(self, value):
        return value in self.browsers

    def consume(self, req, value):
        with self.lock:
            entry = self.nonces.get(value)
            if entry is None:
                return False
            browser, source, expiry = entry
            sent = [c.encode("utf-8", "replace") for c in req.cookies.getlist(COOKIE_NAME)]
            if (expiry <= time.monotonic() or source != origin(req) or browser not in self.browsers
                    or not any(secrets.compare_digest(browser.encode(), c) for c in sent)):
                return False
            del self.nonces[value]
            return True

    def retry_after(self):
        now = time.monotonic()
        while self.failures and self.failures[0] <= now - FAILED_ATTEMPT_WINDOW:
            self.failures.popleft()
        if len(self.failures) >= FAILED_ATTEMPT_LIMIT:
            return max(1, int(self.failures[0] + FAILED_ATTEMPT_WINDOW - now) + 1)
        return 0

    def failed(self):
        self.failures.append(time.monotonic())
