"""Home Air as an MCP server, at /mcp on the running app, so an AI assistant can ask about this home.

MCP over Streamable HTTP in its simplest form: each POST carries JSON-RPC and gets one JSON answer, no session and no
event stream. It speaks the handshake-era versions (`initialize`, up to 2025-11-25). A request whose MCP-Protocol-Version
header names another version (a 2026-07-28 client's `server/discover` probe) gets HTTP 400 with a plain JSON-RPC error,
the answer after which a 2026 client falls back to `initialize`. The tools and their answers live in agent_tools.py,
shared with the skill's routes. Read-only.

Who can call it: whoever can reach the app, as for /api/current. On this computer the app listens on 127.0.0.1 unless HOST
says otherwise; a Railway or Docker deploy is reachable from the internet, with no sign-in. A browser page from another
site can't call it (the Origin check below, against DNS rebinding).

Add it in Claude Code: claude mcp add --transport http homeair http://localhost:5555/mcp
"""
import json
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, Response, current_app, jsonify, request

import agent_tools

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")   # newest first: the handshake-era versions this answers in
SERVER_VERSION = "0.1.0"
LOCAL_ORIGIN_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
PASS_ON = ("moment", "sample", "monitor")   # the outer request's demo choices, kept for the app's own routes
INSTRUCTIONS = ("Home Air reads one home's air monitors and outdoor data, and lists what to do and when, each with "
                "its reason. Call home_air_now for any 'should I' question about this home's air, and "
                "home_air_history for how it has been. "
                "Home Air's actions are its rules applied to the readings and this home's limits: use them as the "
                "starting answer, then think it through with the readings, the history and what you know about the "
                "person (who is asking, who lives there, what they told you). Give one answer. If it differs from "
                "Home Air's action, give the reason for the difference in the same sentence (\"Open it at noon: the "
                "exhaust clears at 11 AM, but the baby naps until noon\"), since Home Air's card may show its own "
                "timing beside your words. Use only readings the tools return; never invent one. A cause Home Air "
                "didn't name is a guess and is said as one (\"maybe the cooking\"). "
                "Lead with the answer and its timing in one sentence that stands alone, then the reasons in two or "
                "three plain sentences with rounded numbers, then read.source_line as it is (which monitor and when). "
                "Two to four plain sentences, no tables, headings or lists, about 60 words unless asked for more: the answer may be read in a "
                "terminal, on a phone, in a messaging app or aloud. "
                "If a reading is stale (stale: true), start by saying when it was taken: "
                "'the last reading is from 9:10 PM'. "
                "If an input the question depends on is missing (inputs), add one sentence after the answer saying so, "
                "with its why ('Pollen isn't checked: this Home Air has no pollen key'), only when it could change the "
                "answer; never for an input that is off. "
                "This is information about the air, not medical advice.")

# JSON-RPC error codes (https://www.jsonrpc.org/specification#error_object)
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

bp = Blueprint("mcp", __name__)
_settings = lambda: {}   # the home's page settings (place, limits, temp unit); init() sets the real one


def init(app, settings):
    """Serve /mcp and the skill's routes on `app`. `settings()` returns the page's settings for this request."""
    global _settings
    _settings = settings
    app.register_blueprint(bp)


def _fetch(path, body=None):
    """The app's own JSON for one of its routes, in process: the same code, cache and demo data as the page."""
    keep = {k: request.args[k] for k in PASS_ON if k in request.args}
    if keep:
        path += ("&" if "?" in path else "?") + urlencode(keep)
    with current_app.test_client() as c:
        r = c.post(path, json=body) if body is not None else c.get(path)
    try:
        return json.loads(r.get_data(as_text=True))
    except ValueError:
        return {"error": f"{path.split('?')[0]} answered HTTP {r.status_code}"}


def _origin_ok():
    """No Origin (a CLI, desktop or server-to-server client), or a page on this computer (the MCP Inspector). A page on
    any other site is refused, even one whose name points at this computer (DNS rebinding sends a matching Host too)."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return (urlsplit(origin).hostname or "") in LOCAL_ORIGIN_HOSTS


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_result(name, args):
    try:
        data = agent_tools.call(name, args, _fetch, _settings())
    except agent_tools.ToolError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": data, "isError": False}


def _handle(msg):
    """One JSON-RPC message → its response dict, or None for a notification."""
    if isinstance(msg, dict) and msg.get("jsonrpc") == "2.0" and "method" not in msg and ("result" in msg or "error" in msg):
        return None   # a response from the client: this server sends no requests, so there is nothing to match it to
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "Not a JSON-RPC 2.0 request")
    if "id" not in msg:
        return None   # notifications (initialized, cancelled) need no answer
    msg_id, method, params = msg["id"], msg["method"], msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error(msg_id, INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "homeair", "title": "Home Air", "version": SERVER_VERSION},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": agent_tools.TOOLS}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments")
        if not isinstance(name, str) or name not in {t["name"] for t in agent_tools.TOOLS}:
            return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        if args is not None and not isinstance(args, dict):
            return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
        result = _tool_result(name, args)
    else:
        return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _json(body, status=200):
    r = jsonify(body)
    r.status_code = status
    r.headers["Cache-Control"] = "no-store"   # answers about one home, right now: never kept by a browser or CDN
    return r


@bp.route("/mcp", methods=["POST"])
def mcp():
    if not _origin_ok():
        return _json(_error(None, INVALID_REQUEST, "Origin not allowed"), 403)
    version = request.headers.get("MCP-Protocol-Version")
    if version and version not in PROTOCOL_VERSIONS:
        return _json(_error(None, INVALID_REQUEST, f"Unsupported protocol version {version}; this server speaks "
                                                   f"{', '.join(PROTOCOL_VERSIONS)} through initialize"), 400)
    try:
        msg = json.loads(request.get_data(as_text=True) or "null")
    except ValueError:
        return _json(_error(None, PARSE_ERROR, "Invalid JSON"), 400)
    if isinstance(msg, list):   # a batch (2025-03-26): each request answered, notifications left out
        if not msg:
            return _json(_error(None, INVALID_REQUEST, "Empty batch"), 400)
        answers = [a for a in (_handle(m) for m in msg) if a is not None]
        return _json(answers) if answers else Response(status=202)
    answer = _handle(msg)
    if answer is None:
        return Response(status=202)
    return _json(answer)


@bp.route("/mcp", methods=["GET", "DELETE"])
def mcp_no_stream():
    """No server-sent event stream and no sessions to end: the protocol's answer is 405."""
    return Response(status=405, headers={"Allow": "POST"})


@bp.route("/api/agent/now")
def agent_now():
    """The skill's `now`: the same answer as the home_air_now tool."""
    return _route(lambda: agent_tools.now(_fetch, _settings()))


@bp.route("/api/agent/history")
def agent_history():
    """The skill's `history`: ?hours=1..168&where=indoor|outdoor, the same answer as home_air_history."""
    args = request.args
    return _route(lambda: agent_tools.history(_fetch, _settings(), args.get("hours", agent_tools.DEFAULT_HOURS),
                                              args.get("where", "indoor")))


def _route(run):
    try:
        return _json(run())
    except agent_tools.ToolError as e:
        return _json({"error": str(e)}, 409)
