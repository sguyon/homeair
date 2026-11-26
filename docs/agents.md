# Connect AI assistants to Home Air

> [!WARNING]
> **Experimental.** The skill has been tried with Claude Code and Codex, and the MCP server with claude.ai and ChatGPT; the other assistants on this page haven't been tried. If a step fails, please [open an issue](https://github.com/sguyon/homeair/issues) with the assistant's name, the step and what the assistant said.

No Home Air yet? [Install it first](../README.md#installation).

Two ways to connect an AI assistant:

- **The skill**: for coding agents on the computer with Home Air's folder. With it, they can also change your settings when you ask.
- **The MCP server**: at `<your Home Air address>/mcp`, for other assistants. Read-only. claude.ai and ChatGPT need Home Air with remote access over HTTPS (on [Railway](../DEPLOYMENT.md#railway), for example).

## Coding agents on this computer (Claude Code, Codex, Cursor, Gemini CLI)

In Home Air's folder, run this command. It adds the skill to the coding agents on this computer:

```
./homeair skill add
```

If Home Air runs on another computer, add its address: `./homeair skill add --url <your Home Air address>`.

Then open a new session in the agent and ask "How is the air inside now?"

To see where the skill is: `./homeair skill status`. To remove it: `./homeair skill remove`.

## Claude (web, desktop and mobile apps)

1. On claude.ai or in Claude Desktop, open **Plugins › Add › Add custom connector**.
2. Name: `Home Air`.
3. URL: your Home Air's HTTPS address with remote access, followed by `/mcp` (for example `https://<name>.up.railway.app/mcp`).
4. Choose **No sign-in**, then **Add**.
5. In a new chat, ask "How is the air at home? Should I open a window?"

## ChatGPT (web, desktop and mobile apps)

1. Open **Plugins › Add › Add custom MCP server**.
2. Name: `Home Air`.
3. Connection URL: your Home Air's HTTPS address with remote access, followed by `/mcp` (for example `https://<name>.up.railway.app/mcp`).
4. Authentication: **No authentication**.
5. Select the box "I understand and want to continue", then **Create as a plugin**.
6. In a new chat, ask "How is the air at home? Should I open a window?"

## Other agents

Ask the agent, with your Home Air address:

```text
Add Home Air's MCP server at <your Home Air address>/mcp, following https://github.com/sguyon/homeair/blob/main/docs/agents.md
```

<details>
<summary>The command for each agent</summary>

| Agent | Command or setting |
|---|---|
| Claude Code ([docs](https://code.claude.com/docs/en/mcp)) | `claude mcp add --transport http --scope user homeair <address>/mcp` |
| Codex ([docs](https://learn.chatgpt.com/docs/extend/mcp)) | `codex mcp add homeair --url <address>/mcp` |
| Gemini CLI ([docs](https://geminicli.com/docs/tools/mcp-server/)) | `gemini mcp add --transport http --scope user homeair <address>/mcp` |
| Cursor ([docs](https://cursor.com/docs/mcp)) | in `~/.cursor/mcp.json`: `{"mcpServers": {"homeair": {"url": "<address>/mcp"}}}` |
| OpenClaw ([docs](https://docs.openclaw.ai/tools/mcp)) | `openclaw mcp add homeair --url <address>/mcp --transport streamable-http` |
| Hermes ([docs](https://hermes-agent.nousresearch.com/docs)) | in `~/.hermes/config.yaml`: `mcp_servers: {homeair: {url: "<address>/mcp"}}` |

An agent on another computer needs an address it can reach: Home Air on a home server (`http://<the server's name or IP address>:8000`), or Home Air on this computer opened to your Wi-Fi with `HOST=0.0.0.0 ./homeair start-at-login on` (`http://<this computer's name>.local:5555`). Anyone on your Wi-Fi can then read it too.

</details>

<details>
<summary>Claude Desktop with Home Air on this computer or on a home server</summary>

Claude Desktop reaches a local address through a bridge, `mcp-remote`, which needs Node ([docs](https://modelcontextprotocol.io/docs/develop/connect-local-servers)). In Settings › Developer › Edit Config, add these lines, then quit Claude Desktop and open it again:

```json
{"mcpServers": {"homeair": {"command": "npx", "args": ["-y", "mcp-remote", "<address>/mcp", "--allow-http"]}}}
```

`--allow-http` lets `mcp-remote` use an `http://` address other than localhost ([mcp-remote](https://github.com/geelen/mcp-remote)).

</details>

<details>
<summary>More about the skill</summary>

- `./homeair skill add` links the skill into `~/.agents/skills` (Codex, Cursor, Gemini CLI) and `~/.claude/skills` (Claude Code), and never replaces a skill that is already there. The links point to Home Air's folder, so updating Home Air updates the skill.
- To read Home Air on this computer again after `--url`: `./homeair skill add --url local`. `./homeair skill remove` also forgets the `--url` address.
- On another computer: `git clone https://github.com/sguyon/homeair`, then `./homeair skill add --url <address>` in the `homeair` folder.
- Hermes: `ln -s "$PWD/skills/home-air-quality" ~/.hermes/skills/home-air-quality` from Home Air's folder ([docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)).
- OpenClaw copies skills, and its copy reads only `http://localhost:5555`: use the MCP server instead.
- Windows: an agent that runs on Windows itself doesn't read the skills folder of Linux on Windows (WSL): use the MCP server instead.

</details>
