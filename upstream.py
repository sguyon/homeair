"""Every call to an outside API goes through get(): it counts the call against the source's daily budget (budgets.py),
says who is calling (USER_AGENT), and remembers failures so a struggling API gets fewer calls, not more: a 429 or 503
pauses every source on that host for its Retry-After; a 5xx, a timeout or no network backs that source off 2 → 30
minutes. Concurrent requests for the same data already share one call (sources.cached)."""

import time
from urllib.parse import urlparse

import requests

import budgets

APP_VERSION = "v0.1"   # the release (server.py shows it on the page's build line)
USER_AGENT = f"HomeAir/{APP_VERSION.lstrip('v')}"   # the version comes from APP_VERSION, so a release bump updates it
_HOST_OF = {}      # source → the host it last called, for usage(): which sources a host's pause holds
_HOST_UNTIL = {}   # host → when its 429/503 pause ends: every source on that host waits, even one not seen yet


def headers(extra=None):
    return {"User-Agent": USER_AGENT, **(extra or {})}


def get(url, source, counted=False, **kw):
    """requests.get(url, **kw) for `source` (a budgets.DAILY name), with the User-Agent. Raises budgets.BudgetExceeded
    (a RuntimeError) without calling when the source is over its budget or paused; otherwise returns the response as
    requests does (raise_for_status is the caller's), after noting how it went. counted: the caller already spent
    the call (budgets.spend), so only a pause is checked here."""
    host = urlparse(url).netloc
    _HOST_OF[source] = host
    if _HOST_UNTIL.get(host, 0) > time.time():
        raise budgets.BudgetExceeded(f"{host} asked us to wait {int(_HOST_UNTIL[host] - time.time())} s more")
    budgets.check(source) if counted else budgets.spend(source)
    kw["headers"] = headers(kw.get("headers"))
    try:
        r = requests.get(url, **kw)
    except requests.exceptions.RequestException:
        budgets.failed(source)
        raise
    status = getattr(r, "status_code", 200)
    if status in (429, 503):
        names = sorted({s for s, h in _HOST_OF.items() if h == host} | {source})
        seconds = budgets.told_to_wait(names, status, (getattr(r, "headers", None) or {}).get("Retry-After"))
        _HOST_UNTIL[host] = time.time() + seconds
    elif status >= 500:
        budgets.failed(source)
    elif status < 400:
        budgets.succeeded(source)
    return r
