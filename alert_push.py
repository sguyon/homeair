"""Sending one web push to one phone. Never raises: the answer is "ok", "gone" (the phone dropped it) or "error"."""
import json
import logging
import os
from urllib.parse import urlsplit

import requests
from pywebpush import WebPushException, webpush

from config import env_key

log = logging.getLogger("aq.alert_push")

PUSH_TTL_S = 3600  # seconds the push service keeps an undelivered alert: after an hour it is stale.
PUSH_TIMEOUT_S = 10  # seconds to wait for the push service.
VAPID_SUB = "https://homeair.app"  # the project's public site: the push service's contact claim, never a personal address.
PUSH_HOSTS = ("web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com")  # Safari and iOS, Chrome and Android, Firefox: the only addresses a phone may subscribe with, so the server never posts to a stranger's address.
GONE_STATUSES = (404, 410)  # the push service says this subscription no longer exists.


def push_host_allowed(endpoint):
    """True when the address is https on one of PUSH_HOSTS, or a subdomain of one (an exact host or a dot-suffix).
    urlsplit and requests (urllib3) read some addresses differently ("https://127.0.0.1\\@fcm.googleapis.com/x"), so a
    backslash, an "@" or userinfo is refused, and the host requests would connect to must equal urlsplit's."""
    if not isinstance(endpoint, str) or "\\" in endpoint or "@" in endpoint or any(c.isspace() or ord(c) < 32 for c in endpoint):
        return False
    try:
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").lower()
        parts.port   # a bad port raises
        if parts.username is not None or parts.password is not None:
            return False
        sent_to = (urlsplit(requests.Request("POST", endpoint).prepare().url).hostname or "").lower()
    except (ValueError, requests.RequestException):
        return False
    return (parts.scheme == "https" and host == sent_to
            and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS))


def _session():
    """A requests session that follows no redirect (a redirect raises): a push service never redirects, and a 307 could
    send the server to an address inside the network."""
    session = requests.Session()
    session.max_redirects = 0
    return session


def public_key():
    """The base64url public key the browser subscribes with, or None before setup made the keys."""
    return env_key("VAPID_PUBLIC_KEY", os.environ) or None


def _host(subscription):
    try:
        return urlsplit(str(subscription.get("endpoint", ""))).hostname or "unknown host"
    except (ValueError, AttributeError):
        return "unknown host"


def send(subscription, title, body, url="/#summary"):
    if not push_host_allowed(str(subscription.get("endpoint", "")) if isinstance(subscription, dict) else ""):   # rows stored before the check
        log.warning("push not sent: %s is not a push service", _host(subscription))
        return "error"
    private = env_key("VAPID_PRIVATE_KEY", os.environ)
    if not private:
        log.warning("push not sent: no VAPID key")
        return "error"
    try:
        answer = webpush(subscription_info=subscription, data=json.dumps({"title": title, "body": body, "url": url}),
                         vapid_private_key=private, vapid_claims={"sub": VAPID_SUB}, ttl=PUSH_TTL_S,
                         requests_session=_session(), headers={"Urgency": "high"}, timeout=PUSH_TIMEOUT_S)
        # the push service's own answer: host and status only
        log.info("push to %s: %s", _host(subscription), getattr(answer, "status_code", "?"))
        return "ok"
    except requests.TooManyRedirects:   # a push service never redirects: this address is no phone's
        log.warning("push to %s was redirected: dropping the phone", _host(subscription))
        return "gone"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in GONE_STATUSES:
            return "gone"
        log.warning("push to %s failed: %s", _host(subscription), status or "no answer")
    except Exception as exc:   # a timeout, a refused connection, a malformed subscription
        log.warning("push to %s failed: %s", _host(subscription), type(exc).__name__)
    return "error"
