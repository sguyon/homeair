"""/mcp: an AI assistant's connection. What it must never do silently: speak a broken protocol, answer a page from
another site, or hand over the token, coordinates or serial numbers."""
import json
import re
from datetime import datetime, timedelta, timezone

import agent_tools
import server
import sources
from test_sources import wifi  # noqa: F401  (the fixture: a home with serials and a stub poller)

MCP = "/mcp"


def rpc(client, method, params=None, msg_id=1, **headers):
    body = {"jsonrpc": "2.0", "method": method, **({"id": msg_id} if msg_id is not None else {}), **({"params": params} if params is not None else {})}
    return client.post(MCP, json=body, headers=headers)


def test_handshake_and_read_only_tools(client):
    init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).get_json()
    assert init["result"]["protocolVersion"] == "2025-06-18" and "tools" in init["result"]["capabilities"]
    assert rpc(client, "notifications/initialized", msg_id=None).status_code == 202
    tools = rpc(client, "tools/list").get_json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"home_air_now", "home_air_history"}
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools)
    probe = rpc(client, "server/discover", **{"MCP-Protocol-Version": "2026-07-28"})
    assert probe.status_code == 400 and probe.get_json()["error"]["code"] == -32600   # a 2026 client then falls back to initialize
    assert client.get(MCP).status_code == 405
    for bad in ([], {"name": {"x": 1}}, {"name": "home_air_history", "arguments": []}):   # malformed calls answer in JSON-RPC
        assert rpc(client, "tools/call", bad).get_json()["error"]["code"] == -32602


def test_a_page_on_another_site_is_refused(client):
    assert rpc(client, "ping", Origin="http://evil.example").status_code == 403
    assert rpc(client, "ping", Origin="http://evil.example:5555", Host="evil.example:5555").status_code == 403   # DNS rebinding
    assert rpc(client, "ping", Origin="http://localhost:6274").status_code == 200   # the MCP Inspector on this computer


def test_agent_route_says_why_it_cannot_answer(client, monkeypatch):
    monkeypatch.setattr(server, "MODE", "no_token")   # the skill's script reads the 409's error and says it
    answer = client.get("/api/agent/now")
    assert answer.status_code == 409 and "no AirGradient token" in answer.get_json()["error"]


def test_answer_has_readings_but_no_token_coordinates_or_serial(client, monkeypatch):
    monkeypatch.setattr(server, "TOKEN", "SECRET123")
    monkeypatch.setattr(server, "MODE", "live")
    row = {"locationId": 1, "locationName": "Bedroom", "locationType": "indoor", "serialno": "84fce6XXXX", "firmwareVersion": "3.7.0",
           "latitude": 39.95, "longitude": -75.16, "timestamp": "2026-11-21T03:00:00.000Z", "rco2": 1250, "pm02": 4, "rhum": 48, "atmp": 21}
    monkeypatch.setattr(sources, "current_rows", lambda force=False: ([row], {"state": "fresh", "age": 0}))
    result = rpc(client, "tools/call", {"name": "home_air_now", "arguments": {}}).get_json()["result"]
    assert result["isError"] is False and result["structuredContent"]["readings"]["indoor"]["co2"] == 1250
    indoor = result["structuredContent"]["readings"]["indoor"]
    # days old: the server says it's stale, so the agent says when before anything else (not a rule in two prompts)
    assert indoor["minutes_ago"] > agent_tools.STALE_READING_MINUTES and indoor["stale"] is True and "age_minutes" not in indoor
    row["timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    fresh = rpc(client, "tools/call", {"name": "home_air_now", "arguments": {}}).get_json()["result"]["structuredContent"]
    assert fresh["readings"]["indoor"]["minutes_ago"] == 5 and fresh["readings"]["indoor"]["stale"] is False
    # an agent took ids 1 and 2 (placeholders for monitors read on the Wi-Fi) for the demo's data (23 Nov): no ids, and
    # the answer says whether it's the demo
    assert "id" not in result["structuredContent"]["readings"]["indoor"] and result["structuredContent"]["home"]["demo"] is False
    assert result["structuredContent"]["read"]["source_line"].startswith("Bedroom monitor, ")   # ready to end the answer with
    text = json.dumps(result)
    for secret in ("SECRET123", "84fce6", "3.7.0", "39.95", "75.16", "serialno", "latitude"):
        assert secret not in text, secret


def test_agent_history_works_without_a_cloud_id(wifi, monkeypatch, tmp_path):
    """A home with no token and no cloud id: the agent tool's history comes from the home's own store, and its answer holds no serial or address."""
    from test_sources import stored
    stored(tmp_path, monkeypatch)
    client = wifi({"indoor": "local", "outdoor": "local"}, token="")
    result = rpc(client, "tools/call", {"name": "home_air_history", "arguments": {"hours": 24}}).get_json()["result"]
    assert result["isError"] is False and result["structuredContent"]["samples"] > 0
    text = json.dumps(result)
    assert "84fce6" not in text and not re.search(r"\d+\.\d+\.\d+\.\d+", text)
