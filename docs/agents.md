# Connect your AI assistant

**Context:** Home Air is an app for your AirGradient air quality monitors: a page with your readings, the weather, pollen and smoke, and what to do about them (open a window, run a purifier). The steps below connect an AI assistant to a Home Air you already run, so the assistant answers from your home's readings. No Home Air yet: [install it first](../README.md#installation).

> [!WARNING]
> **Experimental.** The skill and the MCP server are only partly tested: the skill has been tried with Claude Code and Codex, and the other assistants on this page haven't been tried. If a step fails, please [open an issue](https://github.com/sguyon/homeair/issues) with the assistant's name, the step and what the assistant said.

## Find your assistant

1. **Claude Code, Codex, Cursor or Gemini CLI, on the computer with Home Air's folder**
   - Use [the skill](#the-skill).
   - In Home Air's folder, run `./homeair skill add`.
2. **Another agent that runs commands** (OpenClaw, Hermes, or one on another computer)
   - Use [the MCP server](#the-mcp-server).
   - Ask the agent, with your `<address>`:
     ```text
     Add Home Air's MCP server at <address>/mcp, following https://github.com/sguyon/homeair/blob/main/docs/agents.md
     ```
3. **claude.ai, Claude Desktop or Claude's phone apps**
   - Use [the MCP server](#the-mcp-server), with Home Air [online](../DEPLOYMENT.md#railway).
   - Put Home Air online, then add a custom connector with `https://<name>.up.railway.app/mcp`.
4. **Claude Desktop, with Home Air on this computer or on a home server**
   - Use [the MCP server](#the-mcp-server) through a bridge: the last row of its table.

`<address>` is where Home Air runs: see [Your Home Air address](#your-home-air-address).

The skill and the MCP server give the same answers: the readings, your home's limits and the actions, each with a why. Never your token, the home's coordinates or the monitors' serial numbers. Anyone who can open the page can read the same answers. Both need Home Air running.

## Your Home Air address

| Where Home Air runs | `<address>` | Who can reach it |
|---|---|---|
| This computer | `http://localhost:5555`, or the port `./homeair status` shows | This computer only |
| This computer, after `HOST=0.0.0.0 ./homeair start-at-login on` | `http://<the computer's name>.local:5555` | Every device on your Wi-Fi |
| A home server, with Docker | `http://<the server's name or IP address>:8000` | Every device on your Wi-Fi |
| Online, on Railway ([DEPLOYMENT.md](../DEPLOYMENT.md#railway)) | `https://<name>.up.railway.app` (`railway domain list --service homeair`) | Anyone, including Claude's apps |

## The skill

For coding agents on a computer with Home Air's folder. With it, the agent can also change your settings (`config.toml`), only when you ask and say yes.

1. In Home Air's folder, run `./homeair skill add`. It links the skill into `~/.agents/skills` (Codex, Cursor, Gemini CLI) and `~/.claude/skills` (Claude Code), and never replaces a skill that is already there. When Home Air runs on a home server or online, run `./homeair skill add --url <address>` instead. To read Home Air on this computer again: `./homeair skill add --url local`.
2. Open a new session in the agent and ask "How is the air inside now?": the agent answers from your home's readings.

- **On another computer:** `git clone https://github.com/sguyon/homeair`, then `./homeair skill add --url <address>` in the `homeair` folder.
- **Check or remove:** `./homeair skill status`; `./homeair skill remove` removes the links `skill add` made and the saved `--url` address.
- **Hermes:** `ln -s "$PWD/skills/home-air-quality" ~/.hermes/skills/home-air-quality` from Home Air's folder ([docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)).
- **OpenClaw** copies skills, and its copy reads only `http://localhost:5555`: use the MCP server instead.
- **Windows:** an agent that runs on Windows itself doesn't read the skills folder of Linux on Windows (WSL): use the MCP server instead.

## The MCP server

Two read-only tools at `<address>/mcp`: `home_air_now` and `home_air_history`. The MCP server is part of Home Air, so there is nothing to install.

| Assistant | Add it with |
|---|---|
| Claude Code ([docs](https://code.claude.com/docs/en/mcp)) | `claude mcp add --transport http --scope user homeair <address>/mcp` |
| Codex ([docs](https://learn.chatgpt.com/docs/extend/mcp)) | `codex mcp add homeair --url <address>/mcp` |
| Gemini CLI ([docs](https://geminicli.com/docs/tools/mcp-server/)) | `gemini mcp add --transport http --scope user homeair <address>/mcp` |
| Cursor ([docs](https://cursor.com/docs/mcp)) | in `~/.cursor/mcp.json`: `{"mcpServers": {"homeair": {"url": "<address>/mcp"}}}` |
| OpenClaw ([docs](https://docs.openclaw.ai/tools/mcp)) | `openclaw mcp add homeair --url <address>/mcp --transport streamable-http` |
| Hermes ([docs](https://hermes-agent.nousresearch.com/docs)) | in `~/.hermes/config.yaml`: `mcp_servers: {homeair: {url: "<address>/mcp"}}` |
| claude.ai, Claude Desktop, Claude's phone apps ([help](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)) | Home Air online only: a custom connector with `<address>/mcp`. Claude reaches it from Anthropic's servers. Untested with Home Air. |
| Claude Desktop, Home Air on this computer or on a home server ([docs](https://modelcontextprotocol.io/docs/develop/connect-local-servers)) | Through the bridge `mcp-remote`, which needs Node: the lines below, in Settings › Developer › Edit Config. Then quit Claude Desktop and open it again. |

```json
{"mcpServers": {"homeair": {"command": "npx", "args": ["-y", "mcp-remote", "<address>/mcp", "--allow-http"]}}}
```

`--allow-http` lets `mcp-remote` use an `http://` address other than localhost ([mcp-remote](https://github.com/geelen/mcp-remote)).
