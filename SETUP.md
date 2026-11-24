# Setup

**Done when** `scripts/run.sh scripts/setup.py` ends with `Live OK` (monitors found: the page answers with the home's settings and each monitor has a recent reading), or with "Nothing is read yet" (no monitor found: the page is open on the token step, which is the next step, not a failure).

For a coding agent setting this up for someone's home. Do the steps yourself when a command can. Setup asks the user nothing: don't ask for an address, profiles or a token before running it (the page asks for them afterwards, and they are all optional). Don't change the app's code during setup. Never ask for a key in chat, read or print `.env`, or commit private settings. Ask before changing a live service or account.

## Get ready

Tested on Mac and Linux. On Windows, use WSL or Docker.

Work in the Home Air folder throughout: `~/homeair`, unless the user named another folder. Never inside the project that happens to be open. If it isn't there yet:

```bash
git clone https://github.com/sguyon/homeair ~/homeair
cd ~/homeair
```

Run every command below from this folder through `scripts/run.sh`: it uses uv, installs uv if it's missing, or falls back to Python 3.11 or newer, and says which; if none works, it stops and says what to install. To see what's done and the next command at any point, run `scripts/run.sh scripts/setup.py --status`; it changes nothing. Some commands wait on purpose (`--wait-token` up to 90 seconds, `--start` up to 30, `--start-at-login on` up to 60 on a Mac, `--live` up to 10 for an app just started): run them with a timeout of at least 100 seconds, so one call returns the result; don't poll.

In a cloud sandbox (Claude Code on the web, Codex cloud) asked to set up a home: say the app can't stay there and to run setup on their own computer, then stop. Development in a sandbox is unaffected: see AGENTS.md, "Run".

## Set it up

One command, no question:

```bash
scripts/run.sh scripts/setup.py
```

It takes about a minute (it waits at most 3 s for the monitors, up to 30 s for the app, and on a Mac up to 60 s for the user to answer macOS's prompt). Run it with a timeout of at least 150 seconds, so one call returns the result; don't poll. On a Mac, say in the same message that you're running it: "macOS will show two notices naming "sh" and "uv": both are Home Air, which starts through them. Choose Allow." It:

1. looks for AirGradient monitors on this computer's Wi-Fi, reads each one once and writes `config.toml`: serials, a default name, and the unit the monitor uses; no place, and nobody picked in "Who lives here" yet;
2. starts the app in the background, and makes it start again when the user logs in;
3. adds Home Air's skill to your skills (linked into your skills folders; `--agent off` removes it);
4. opens the page in the user's browser (without a token, through the private link, so that browser can open the token box later);
5. checks the page and each monitor's reading, and ends with `Live OK`.

What its lines mean:

- `indoor: found on your Wi-Fi, read directly`: that monitor is read on the Wi-Fi, one reading a minute. `Also found: another indoor monitor (…1a2b, I-9PSL). To read it instead: … --monitor <serial>`: setup reads one monitor of each kind, the one already in `config.toml`, else the lowest serial; run the command it names if the user wants the other one (it checks that monitor is on the Wi-Fi, saves the change with a backup and restarts the app). A line saying a monitor's firmware is older than 3.1.4, or its model isn't one setup reads: that monitor is not read; say so to the user.
- `Settings: <path>`: the file written. An existing `config.toml` is copied to `config.toml.bak-<time>` first and keeps the household's own choices.
- `Started: http://localhost:5555`: the page's address (a free port from 5600 up when 5555 is taken). Use the address it prints.
- `Nothing is read yet`, after "No AirGradient monitor answered on this network": nothing was found, which is an ordinary home. The page is open on the token step. The user enters their AirGradient token there (it adds the home's monitors from the account, and the last 7 days); you never see it. Say "I'll notice when the token is saved: no need to reply. If nothing happens, type done." and in the same turn run `scripts/run.sh scripts/setup.py --wait-token`: it waits up to 90 seconds and prints what the account has, without the token (if it says "Not saved yet", run it again, or once more when they type done). The page where they saved it turns into the home by itself: don't open another tab. If there's no monitor yet, they can set one up with AirGradient's instructions; meanwhile [the demo](https://homeair.app) shows any public monitor ("Near me").
- `WSL can't see your Wi-Fi's monitors`, `a container can't see your Wi-Fi's monitors`, `a hosted copy can't see your Wi-Fi's monitors`: setup can't look from there. In WSL the token path above works as is; for Docker see `--home-server` below. In a cloud sandbox (Claude Code on the web, Codex cloud) the app can't stay: say to run setup on their own computer, then stop.
- `Waiting for the first reading on your Wi-Fi … press Allow`: the copy that starts at login waits for macOS's prompt "Allow "uv" to find devices on local networks?". `No reading on your Wi-Fi after 60 s`: it wasn't allowed; tell the user to turn on "uv" in System Settings › Privacy & Security › Local Network, then run `--live`.
- `Start at login isn't available here (…)`: the app runs now and stops when the computer restarts. Say so; `--start` starts it again.
- A `Problem:` line or `Not live yet`: the line says what to fix (a monitor offline, a reading older than a few minutes). Fix it and run the command again: it is safe to repeat.

Place and who lives here are set on the page, or by you through "Change your home" in [AGENTS.md](AGENTS.md) when the user tells you (`setup.py --address "<address>" --profiles <list>` also does it, with an AirGradient token saved). A token entered later on the page matches the monitors by serial and fills the last 7 days (a monitor found on the Wi-Fi that isn't in the account, such as a neighbour's on a shared network, is replaced by the account's monitor of that kind); it is optional, and needed for putting the home online.

If the token page can't open, the user can put the token in `.env` themselves (`open -e .env` on Mac, `xdg-open .env` on Linux) and restart. Check only set or missing with `scripts/check_config.py`. Can't find the token: the page's "Show me where" under step 1 opens a marked picture of AirGradient's settings (General Settings, Connectivity, API Access); walk them through it in words.

## Start again, check, and connect

The setup command already started the app, set it to start at login, added the skill and checked the page. Run these alone when needed:

- `scripts/run.sh scripts/setup.py --start`: starts the app in the background (it stops a copy it started before and waits until the app answers; when the app runs at login, the system restarts that copy instead). The log is `app.log`. Without a saved token it prints the private link to the token box; `scripts/run.sh scripts/setup.py --connect` prints it again. Don't post the private `/connect?code=…` link it prints: it lets anyone who has it enter a token. Open it with `open '<link>'` on Mac or `xdg-open '<link>'` on Linux; run the start command again to restart it.
- `scripts/run.sh scripts/setup.py --live`: read-only GET requests against the copy `--start` started (for any other page, Docker or Railway, pass its address: `--live <address>`); ends with `Live OK`. Docker and Railway users verify their final URL without starting a local server.
- `scripts/run.sh scripts/setup.py --agent on` (or `off`, `status`): links the skill in `skills/home-air-quality` into your skills folders (Claude Code's `~/.claude/skills`, and the shared `~/.agents/skills` that Codex, Cursor and Gemini CLI read), as links, so updating Home Air updates it. It reads the home's air, and changes `config.toml` only when the user asks and says yes (AGENTS.md, "Change your home"). It never replaces a skill that's already there and says so. Only for a page on this computer; for Docker or Railway, [docs/agents.md](docs/agents.md) says how to point an assistant at another address.
- `scripts/run.sh scripts/setup.py --start-at-login on` (or `off`): a Mac, or Linux with systemd; elsewhere it says why not.
- `scripts/run.sh scripts/setup.py --status`: what's done and the next command; changes nothing.

If a reading is missing, check the monitor is online and set as indoor or outdoor in AirGradient.

## Use it again

End with a short message in this form, with the real address, and nothing after it. When monitors were found, start with the home's own answer: run `python3 -I skills/home-air-quality/scripts/home_air.py now` from this folder (it doesn't need the skill to be loaded in this session) and say what it says in one bold line:

> **Your bedroom air is fine: CO₂ 650.**
>
> Home Air is open at http://localhost:5555 and starts by itself when you log in. Set your place there for weather and pollen, and who lives here for tailored actions.
>
> I've added Home Air's skill to my skills on this computer: ask me about the air at home. I change your settings only when you ask and say yes. The first time, I'll ask your permission to run its script. To remove the skill, ask me.

Adjust it to what happened: with no monitor found, the first line is the not-found line setup printed and the second says to enter the AirGradient token on the page; leave out "starts by itself" when start at login wasn't available, and the paragraph about the skill when `--agent` said "The skill wasn't added". The address goes alone on its line or inside the sentence, plain; don't say a tab is open. If they ask:

- For a home server: `scripts/run.sh scripts/setup.py --home-server`, then pass on its two steps; the server needs Docker. Don't offer to copy it over SSH: give the steps, and do more only if they ask. Once they've started it, check `scripts/run.sh scripts/setup.py --live http://<the server>:8000` if you can reach it.
- Online: [DEPLOYMENT.md](DEPLOYMENT.md#railway).
- Other AI assistants: [docs/agents.md](docs/agents.md) in this folder.

For later sessions, not for the message: the app writes to `app.log` in this folder, and starts again with `cd ~/homeair && scripts/run.sh scripts/setup.py --start` (with `PORT=` first if it isn't 5555), and by itself at login.

Change names, profiles, drying equipment (`[home] appliances`) or limits in `config.toml`; [config.example.toml](config.example.toml) explains the settings. Restart after editing config or `.env` by hand. Keep a private copy of config; it isn't in git. Optional [pollen, road tracing, new profiles and Vercel demo](docs/optional.md); [Railway and Docker deployment](DEPLOYMENT.md).

## On your phone

To open it from a phone on the same Wi-Fi, start it with `HOST=0.0.0.0` before the start command (anyone on that Wi-Fi can then open it), and open `http://<the computer's name>.local:5555` on the phone. Away from home, use the Railway address ([DEPLOYMENT.md](DEPLOYMENT.md)). Then add it to the home screen: Share › Add to Home Screen (iPhone, Safari) or ⋮ › Install app (Android, Chrome); it opens like an app. It works offline only on https, which the Railway address has.
