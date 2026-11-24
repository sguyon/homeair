# Connect your AI assistant

An AI assistant can answer from your home's monitors in two ways, while Home Air is running:

- **The skill**, in `skills/home-air-quality`: short instructions plus a small Python script that gets the readings from the app. For agents that run commands on the computer where Home Air runs. With it, the agent can also change your settings (`config.toml`), only when you ask and say yes.
- **The MCP server**, at `/mcp` on the running app: two read-only tools, `home_air_now` and `home_air_history`. For any assistant that can reach the app's address.

Both give the same answers: the readings, your home's limits and the actions, each with a why. Never your token, the home's coordinates or the monitors' serial numbers; an outdoor reading from a public monitor names its street and distance, as the page does. Anyone who can reach the app's address can read them, as they can open the page.

The examples use `http://localhost:5555`. If setup printed another port, use that one.

## The agent that ran setup

Setup has already added the skill for it: `scripts/run.sh scripts/setup.py --agent on` linked the skill into its skills folder (Claude Code's `~/.claude/skills`, and the shared `~/.agents/skills` that Codex, Cursor and Gemini CLI read). These are links to this folder, so updating Home Air updates the skill. Setup never replaces a skill that's already there.

- See what's linked: `scripts/run.sh scripts/setup.py --agent status`
- Remove it: `scripts/run.sh scripts/setup.py --agent off`. It removes only the links setup made.
- To add it later, or for another agent: `scripts/run.sh scripts/setup.py --agent on`, run by that agent.
- On Windows with WSL: run setup where the agent runs. Links made in WSL's home aren't seen by an agent running on Windows itself.

## Other agents on this computer: the MCP server

Add the address `http://localhost:5555/mcp` (or the port setup printed) to the agent:

- **Claude Code** ([docs](https://code.claude.com/docs/en/mcp)), in every project:

  ```bash
  claude mcp add --transport http --scope user home-air http://localhost:5555/mcp
  ```

- **Codex** ([docs](https://learn.chatgpt.com/docs/extend/mcp)), saved in `~/.codex/config.toml`:

  ```bash
  codex mcp add home-air --url http://localhost:5555/mcp
  ```

- **Gemini CLI** ([docs](https://geminicli.com/docs/tools/mcp-server/)):

  ```bash
  gemini mcp add --transport http --scope user home-air http://localhost:5555/mcp
  ```

- **Cursor** ([docs](https://cursor.com/docs/mcp)): add this to `~/.cursor/mcp.json`:

  ```json
  {"mcpServers": {"home-air": {"url": "http://localhost:5555/mcp"}}}
  ```

- **Claude Desktop** ([docs](https://modelcontextprotocol.io/docs/develop/connect-local-servers)): its settings can only start programs on your computer, so it reaches the address through a small bridge, `mcp-remote`, which needs Node. In Settings › Developer › Edit Config, add the lines below, then quit and reopen it. Claude Desktop's skills run on Anthropic's servers, so they can't reach your computer: use this instead of the skill.

  ```json
  {"mcpServers": {"home-air": {"command": "npx", "args": ["-y", "mcp-remote", "http://localhost:5555/mcp"]}}}
  ```

Both the skill and the MCP server need the app running: `scripts/run.sh scripts/setup.py --start`, or `--start-at-login on` so it starts with your computer.

## OpenClaw and Hermes

**On this computer**, the skill works as for the agents above:

- OpenClaw: `openclaw skills install ./skills/home-air-quality --global` from the Home Air folder (copies it into `~/.openclaw/skills`; [docs](https://docs.openclaw.ai/cli/skills)).
- Hermes: `ln -s "$PWD/skills/home-air-quality" ~/.hermes/skills/home-air-quality` from the Home Air folder (Hermes reads `~/.hermes/skills`; [docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)).

Or the MCP server:

- OpenClaw: `openclaw mcp add home-air --url http://localhost:5555/mcp --transport streamable-http` ([docs](https://docs.openclaw.ai/tools/mcp)).
- Hermes: in `~/.hermes/config.yaml`, under `mcp_servers:`, add `home-air:` with `url: "http://localhost:5555/mcp"` ([docs](https://hermes-agent.nousresearch.com/docs)).

**On another machine at home** (a home server or a Raspberry Pi running the agent): Home Air has to listen on the home network. Start it with `HOST=0.0.0.0` before the start command, or run it with Docker on the home server ([DEPLOYMENT.md](../DEPLOYMENT.md)). Then use `http://<the computer's name>.local:5555/mcp` as the MCP address, or set `HOME_AIR_URL=http://<the computer's name>.local:5555` for the skill. Anyone on that Wi-Fi can then read it too.

## claude.ai and ChatGPT

They reach an MCP server from their companies' servers, by its public address ([claude.ai](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)), so they can't reach Home Air on your computer or your home network. Home Air put online ([DEPLOYMENT.md](../DEPLOYMENT.md)) has a public address; its `/mcp`, like its page, has no sign-in, so anyone with the address can read it. Adding it to these apps hasn't been tested.
