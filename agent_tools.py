"""What an AI assistant gets from Home Air: the tools, their descriptions and the answers' shape.

One place for the MCP server (`mcp_server.py`, at /mcp) and the JSON routes the skill's script calls (/api/agent/now,
/api/agent/history), so every assistant gets the same answer. Read-only. The answers carry readings, the home's limits
and the air read's actions; never coordinates, serial numbers, firmware, tokens or config.

`fetch(path, body=None)` returns the app's own JSON for one of its routes (the server passes an in-process caller).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MAX_HOURS = 168       # hours of history one call may ask for: the page's longest range is a week.
DEFAULT_HOURS = 12    # hours of history when the assistant doesn't say: "last night" from the morning.
UNITS = "PM2.5 ug/m3, CO2 ppm, humidity %, temperature C"
CACHED_HOURS = (1, 6, 24, 168)   # the page's History ranges, which the server caches (sources.HIST_TTL): asked for, then trimmed
STALE_SECONDS = 15 * 60   # a newest reading older than this ends the history window (the demo's fixed clock, a monitor offline)

# The fields read for each measure, first one present wins: the corrected value when the monitor sends it (as static/app.js).
READING_KEYS = {"pm25": ("pm02_corrected", "pm02"), "co2": ("rco2_corrected", "rco2"),
                "temp_c": ("atmp", "atmp_corrected"), "humidity": ("rhum", "rhum_corrected")}
OUTDOOR_KEYS = {**READING_KEYS, "temp_c": ("atmp_corrected", "atmp"), "humidity": ("rhum_corrected", "rhum")}
HISTORY_KEYS = {"pm25": ("pm02_corrected", "pm02"), "co2": ("rco2_corrected", "rco2"), "humidity": ("rhum_corrected", "rhum")}

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {
        "name": "home_air_now",
        "title": "Home air now",
        "description": (
            "The owner's own home air right now, from their Home Air: indoor and outdoor readings (CO2, PM2.5, humidity, "
            "temperature) and what to do, each action with when and why (open or close a window, air a room, run the "
            "purifier or dehumidifier), plus whether the air suits each person type the home chose (toddler, sinus and "
            "allergy, runner). Use it for: is my air OK, is it stuffy, should I open the window, is it OK to run, cook or "
            "sleep with the window open. Readings say when they were measured (UTC). Not for other places, weather "
            "forecasts or medical advice."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Home air now", **READ_ONLY},
    },
    {
        "name": "home_air_history",
        "title": "Home air history",
        "description": (
            "How the owner's home air has been over the last hours or days, from their Home Air: for CO2, PM2.5 and "
            "humidity, the latest, lowest and highest value, when it peaked, and hourly means in the home's own time, "
            "with the home's limits to compare. Use it for: what was my CO2 last night, when did it peak, has it been "
            "damp this week. Not for other places or forecasts."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "minimum": 1, "maximum": MAX_HOURS, "default": DEFAULT_HOURS,
                          "description": f"How many hours back, 1 to {MAX_HOURS} (default {DEFAULT_HOURS})."},
                "where": {"type": "string", "enum": ["indoor", "outdoor"], "default": "indoor",
                          "description": "The indoor or the outdoor monitor (default indoor)."},
            },
            "additionalProperties": False,
        },
        "annotations": {"title": "Home air history", **READ_ONLY},
    },
]


class ToolError(Exception):
    """A plain-words reason the tool can't answer (no token yet, no monitor, a source down)."""


def _pick(row, keys):
    for k in keys:
        v = (row or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
    return None


def _entries(rows):
    """The home's indoor and outdoor rows, as the page picks them (an indoor-only home's outdoor row is its source)."""
    rows = rows if isinstance(rows, list) else []
    indoor = next((r for r in rows if r.get("locationType") == "indoor"), None)
    outdoor = next((r for r in rows if r.get("locationType") == "outdoor"), None) or next((r for r in rows if r.get("source")), None)
    return indoor, outdoor


def _reading(row, keys):
    """One monitor's numbers and when. No coordinates, serial number or firmware: the assistant doesn't need them."""
    if not row:
        return None
    out = {k: _pick(row, v) for k, v in keys.items()}
    out.update(name=row.get("locationName"), id=row.get("locationId"), at=row.get("timestamp"))
    if row.get("source"):
        out["source"] = row["source"]
    return out


def _home(settings):
    return {"timezone": (settings.get("place") or {}).get("timezone") or "UTC", "temp_unit": settings.get("temp_unit", "C"),
            "limits": settings.get("limits", {})}


def _rows(fetch):
    rows = fetch("/api/current")
    if isinstance(rows, dict):
        if rows.get("state") == "no_token":
            raise ToolError("Home Air has no AirGradient token yet: finish setup first.")
        raise ToolError(rows.get("error") or "Home Air couldn't read the monitors.")
    return rows


def now(fetch, settings):
    """Current readings plus the air read (the page's actions with a why), in one answer."""
    i, o = (_reading(r, k) for r, k in zip(_entries(_rows(fetch)), (READING_KEYS, OUTDOOR_KEYS)))
    payload = {"format": "read",
               "indoor_pm25": (i or {}).get("pm25"), "indoor_co2": (i or {}).get("co2"),
               "indoor_humidity": (i or {}).get("humidity"), "indoor_temp_c": (i or {}).get("temp_c"),
               "outdoor_pm25": (o or {}).get("pm25"), "outdoor_humidity": (o or {}).get("humidity"),
               "outdoor_temp_c": (o or {}).get("temp_c"),
               "indoor_location_id": (i or {}).get("id"), "outdoor_location_id": (o or {}).get("id")}
    read = fetch("/api/analyze", payload)
    if not isinstance(read, dict) or read.get("error"):
        raise ToolError((read or {}).get("error") if isinstance(read, dict) else "The air read failed.")
    summary = read.get("summary") or {}
    items = [{"what": it.get("label"), "value": f"{it.get('num')} {it.get('unit')}", "vs_limit": it.get("ctx"),
              "action": (it.get("action") or {}).get("text"), "when": (it.get("action") or {}).get("when"),
              "why": it.get("why")} for it in summary.get("items") or []]
    profiles = [{k: p.get(k) for k in ("name", "state", "ok", "why")} for p in summary.get("profiles") or []]
    for r in (i, o) if settings.get("real_clock", True) else ():   # how old each reading is, so an assistant
        if r and r.get("at"):                                       # without the skill sees a monitor gone quiet;
            # not on the demo's fixed clock, where every reading would look months old
            try:
                r["age_minutes"] = max(0, round((datetime.now(timezone.utc) - _utc(r["at"])).total_seconds() / 60))
            except ValueError:
                pass
    return {"home": _home(settings), "units": UNITS, "readings": {"indoor": i, "outdoor": o},
            "read": {"status": read.get("status"), "written_at": read.get("clock"), "all_clear": summary.get("all_clear"),
                     "actions": items, "profiles": profiles}}


def _hours(value):
    if isinstance(value, bool):
        raise ToolError(f"hours must be a whole number from 1 to {MAX_HOURS}.")
    try:
        return max(1, min(MAX_HOURS, int(value)))
    except (TypeError, ValueError, OverflowError):
        raise ToolError(f"hours must be a whole number from 1 to {MAX_HOURS}.")


def _utc(stamp):
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc)


def history(fetch, settings, hours=DEFAULT_HOURS, where="indoor"):
    """The last `hours` of one monitor, summed up per measure. Times are the home's local time with its UTC offset."""
    hours = _hours(hours)
    if where not in ("indoor", "outdoor"):
        raise ToolError('where must be "indoor" or "outdoor".')
    home = _home(settings)
    zone = ZoneInfo(home["timezone"])
    indoor, outdoor = _entries(_rows(fetch))
    row = indoor if where == "indoor" else outdoor
    if not row or row.get("locationId") is None:
        raise ToolError(f"This home has no {where} monitor with history.")
    now = datetime.now(timezone.utc)
    try:
        newest = _utc(row.get("timestamp"))
    except ValueError:
        newest = now
    stale = (now - newest).total_seconds() > STALE_SECONDS
    end = newest if stale else now   # a live home asks up to now, so the server's cache answers
    span = hours if stale else next((h for h in CACHED_HOURS if h >= hours), MAX_HOURS)
    stamp = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch(f"/api/history/{row['locationId']}?from={stamp(end - timedelta(hours=span))}&to={stamp(end)}")
    if isinstance(rows, dict):
        raise ToolError(rows.get("error", "History isn't available right now."))
    since = end - timedelta(hours=hours)
    out = {"home_timezone": home["timezone"], "where": where, "hours": hours, "until": end.astimezone(zone).isoformat(timespec="minutes"),
           "limits": home["limits"], "units": UNITS, "samples": 0, "metrics": {}}
    for name, keys in HISTORY_KEYS.items():
        pts = []
        for r in rows if isinstance(rows, list) else []:
            v, t = _pick(r, keys), r.get("timestamp")
            if v is None or not t:
                continue
            try:
                at = _utc(t)
            except ValueError:
                continue
            if since <= at <= end:
                pts.append((at, v))
        if not pts:
            continue
        pts.sort(key=lambda p: p[0])
        out["samples"] = max(out["samples"], len(pts))
        hourly = {}
        for t, v in pts:   # by UTC hour, labelled in local time with the offset: no merged hours when clocks go back
            hourly.setdefault(t.replace(minute=0, second=0, microsecond=0), []).append(v)
        local = lambda t: t.astimezone(zone).isoformat(timespec="minutes")
        peak = max(pts, key=lambda p: p[1])
        out["metrics"][name] = {"latest": pts[-1][1], "min": min(v for _, v in pts), "max": peak[1], "max_at": local(peak[0]),
                                "hourly_mean": {local(t): round(sum(v) / len(v), 1) for t, v in sorted(hourly.items())}}
    return out


def call(name, args, fetch, settings):
    """Run one tool by name. Raises ToolError for a plain-words failure, KeyError for an unknown tool."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object.")
    if name == "home_air_now":
        return now(fetch, settings)
    if name == "home_air_history":
        return history(fetch, settings, args.get("hours", DEFAULT_HOURS), args.get("where", "indoor"))
    raise KeyError(name)
