# Agent rules

Rules for any coding agent working in this repo. `CLAUDE.md` is a link to this file.

If `internal/AGENTS.local.md` exists, read it: it holds the maintainer's private rules.

## Setting it up or deploying

- Setting it up for someone's home: follow [`SETUP.md`](SETUP.md).
- Deploying to another computer: follow [`DEPLOYMENT.md`](DEPLOYMENT.md).
- Do the steps yourself when a command can (the `railway` CLI, `gcloud`, `gh`), and say so before listing any manual click-path: the user may act on a list right away. Changing a live service or account needs the user's OK, asked once for all the steps of the task, saying what will be created, sent and made public (not one OK per command: approvals nobody reads protect nothing), and manual steps are the fallback. Never ask the user to do by hand what a command can do.

## Change your home

What the owner asks their agent ("my dad has asthma and lives with us now", "make the CO₂ line stricter", "we got a dehumidifier", "switch to Fahrenheit") is a change to `config.toml`. The skill `skills/home-air-quality` sends agents here. "Set up my home in Home Air" is different: the questions in [SETUP.md](SETUP.md#4-your-home), step 4.

1. **Plan, then wait for a yes.** `./homeair settings show [section]` prints the current value (from `config.toml` only; `show place` for the coordinates). Say in plain words what it is now, what it becomes and what the page will do differently. One change at a time, even when the owner asks for two or says yes to both: each change gets its own plan, backup, edit, check and report, one after the other.
2. **Back up:** `./homeair settings backup` (prints the backup's path: keep it for the undo; keeps the 5 newest).
3. **Edit only what was asked**, in place, keeping comments and order. `config.example.toml` says what each setting means and allows:
   - `[profiles]`: `toddler`, `sinus`, `runner` (`true`/`false`). A person who fits none ("over 65") is a new profile: a code change, not a setting; say so.
   - `[limits]`: stricter CO₂ or PM2.5 is a lower number; the humidity bands stay in order (the check enforces it).
   - `[[roads]]` (one block per road found near the home): `on` is `true` or `false`, one road at a time, by its `key` (`set_road_on` in `config.py`; `./homeair settings show roads` lists them). Never the arcs (`toward`, `toward_calm`) or the other values: setup writes them.
   - `[home]`: `appliances` (`"dehumidifier"`, `"ac_dry"`), `temp_unit` (`"F"` or `"C"`).
   - `[place]`: a new address: ask for it, never guess coordinates, and run `./homeair setup --address "<address>"` (it finds the coordinates, timezone and `pollen_zip`, keeps the monitors, backs up the file; no token needed). config.toml keeps the exact location on this computer; the app rounds it to about 1 km for everything it sends or shows.
   - Monitors: `indoor_serial` / `outdoor_serial` come from `./homeair setup` (found on the Wi-Fi, or matched from the account when a token is saved), and the cloud ids (`indoor_id` / `outdoor_id`) from the account; run `./homeair setup` again for the home. With several monitors of one kind on the Wi-Fi, setup keeps the one in `config.toml`, else the lowest serial, and names each other one with its command: `./homeair monitors use <serial>` reads it instead (a monitor found on the Wi-Fi now; it backs up, drops that kind's cloud id and restarts the app, so skip steps 2 to 5; with a token, only a monitor in the account). With several in the account and none on the Wi-Fi, setup lists them and names the `--indoor-id` / `--outdoor-id` flag. Never typed or invented. `indoor_host` / `outdoor_host` exist only in the `home-server` copy, written by `./homeair home-server`. The outdoor source for a home without an outdoor monitor (`outdoor_from`): from `./homeair check --suggest`.
4. **Check:** `./homeair check --config` prints `Config OK` and no `Problem:` line (a "Not live yet" note only means no token or a stopped app). If not: `./homeair settings restore <the path backup printed>`, say what failed, stop.
5. **Restart and verify:** `./homeair start`, then `./homeair check` (ends with `Live OK`).
6. **Report:** old → new, where the change shows on the page (name the part: the Summary, a person's row, a chart's limit line), and the undo (`./homeair settings restore <the path backup printed>`, then `./homeair start`; the replaced file is kept too).

Never: keys or tokens (they go in `.env`, typed by the owner; see Keys), the rules or the page (code: a reviewed change, with tests), or Home Air on another computer without the steps below.

## After changing config.toml

The deployed copy doesn't follow the local file by itself. After every edit:

1. Check the file: `./homeair check --config` (must end with "Config OK").
2. Update every deployed copy, with the user's OK (it changes a live service):
   - Railway (reads `CONFIG_TOML`): `./homeair railway --service <service>`, once per service. It rounds the place and converts automatic roads' arcs to eight-point sectors; a hand-set `[road]` keeps its arc. It also re-sends the saved keys from `.env` without printing them. The command stages the variables without deploying; follow its printed deploy command under the same approval.
   - Docker (mounts the file): restart the container (`docker compose restart`). A home-server copy (`home-server/`) has its own `config.toml` with the monitors' addresses: run `./homeair home-server` again, then copy the folder over, rather than copying `config.toml` by hand.
3. After deployment, run `./homeair setup --live <url>` (must end with `Live OK`). This checks monitor readings and compares the name, monitors, profiles and outdoor source when configured, not the roads. To confirm a road switch change, open **Your home → Around you** on the deployed page (read-only there) and check the road's switch. Railway's rounded place and automatic-road sectors differ from the local file by design.

If the maintainer's private rules (`internal/AGENTS.local.md`) list services, update all of them.

## Changing the setup

The setup (`SETUP.md`, `scripts/setup.py`, the token page) is a stranger's first contact with the app: keep it as short and automatic as it can be.

- No new questions. The first look (`./homeair setup`) asks nothing; the questions about the home are the ones in SETUP.md's step 4, each with a default and a question tool where the agent has one. Find any other answer from the account, the location or a default; fold anything the user must know into a message they already get (the token step says what's needed, so "I don't have a monitor" is an answer, not an extra question).
- One way to do each step; a fallback only where the main way can't work (Docker, a home server, Railway).
- Each option or branch has to be tested and maintained: prefer removing a failure point to documenting it.
- Make the next action obvious: name the button, link or command, and say what happens after it.
- Show progress and finish with a checked result, the page's address and how to start it again; say clearly when setup cannot continue.
- Keep choices and earlier answers visible where they are needed; don't make the user remember technical details or answer twice.
- When a step fails, explain it in plain words and give one next action that keeps the user's saved work.
- Judge setup changes by whether the task worked and how many actions, questions and minutes it took.

## Keys

- Never read, print or `cat` `.env` or `.env.*`. Repo settings deny it to Claude Code; every other agent follows this line.
- Never ask the user to paste a key into the chat. The user types keys into `.env` themselves, or a command writes them there without showing them (see `SETUP.md`).
- `scripts/check_config.py` says whether each key is set, never its value. Use it instead of looking.
- A paid key gets a cap when it's created: the pollen key is limited to the Pollen API and to 150 calls a day ([`docs/optional.md`](docs/optional.md#pollen)). Check both on an existing key too: `gcloud services api-keys list` and `gcloud beta quotas info list --service=pollen.googleapis.com`.
- Never commit `config.toml`, `.env*` or real readings. `config.toml`, `.env*` and `road-data/` (the downloaded road files) are gitignored, and CI fails a PR that adds them. So are `history.sqlite*` (the home's own readings), `local-addresses.json` and `connect-code` (left by older versions): none goes in git, in an image, in a settings backup or to Railway.

## Layout

- `server.py`: the Flask app, its routes and page, config loading and startup.
- `token_setup.py`: local token entry guards (this computer, an address typed as localhost, 127.0.0.1 or [::1] with the app's port, no proxy or tunnel, not in a container or on a hosted copy), the browser cookie the page sets, one-use form values and rate limit; `templates/token_card.html` is the card.
- `sources.py`: the outside sources, monitor history, outdoor source for indoor-only homes, and their shared cache. It also owns the read order for each monitor, in one place (`home_rows`): the Wi-Fi reading when it is under 90 s old, else the cloud's row when there's a token, else the last stored reading marked stale (`via`: `local`, `cloud` or `stale`; `/health` names each monitor's source by kind). `start_fill` fills the store's gaps from the cloud (7 days, one call per monitor) at start, when a token is saved and when reads resume after a long gap.
- `localnet.py`: monitors on the Wi-Fi. `discover` finds them by mDNS (`zeroconf`, IPv4 only, 3 s at most; never in a container or on a hosted copy), `read_current` reads one (2 s to connect, 2 s to read, 16 KB cap, no redirects, own-network addresses and a matching serial only), the mapper turns its reply into the cloud row's shape, and the `Poller` reads each monitor every 60 s on a background thread whenever the app runs, browses again after 3 failures in a row and pauses a monitor it can't find for 10 min. It browses once at start too: `/health`'s `also_found` lists the other monitors found by kind (the serial's last 4 and the model, never an address). Calls count in the `ag_local` budget. `AIRGRADIENT_LOCAL_FOUND` (a JSON file of simulated monitors) is for tests and `scripts/fake_airgradient.py`.
- `history_store.py`: the home's own history, SQLite (`history.sqlite` next to `config.toml`, or `HISTORY_FILE`; owner-only). One row per read per monitor, keyed by serial; cloud rows (each the five minutes from its time) only fill gaps, never overlap or replace a stored row, and only once their five minutes have ended; nothing is deleted. Charts, the summary and the agent tools read it for a monitor that has rows, up to its last reading; when reads have stopped, the time after it comes from the cloud with a token, else the chart ends there (`sources.store_rows`).
- `address_memory.py`: each monitor's last good address and port, kept in `local-addresses.json` beside the store so a restart reads it at once (never rewrites `config.toml`).
- `air_read.py`: builds the page’s air read, profiles, source facts for the summary, and summary inputs.
- `summary.py`: the rules that pick the actions, the profiles' states and the day chart's data. No network.
- `phrases.py`: the sentences behind each explanation, filled from the readings and the limits.
- `alert_rules.py`: the rules for phone alerts: which of the Summary's actions are due for a phone (an action is due now, at its start, or `WINDOW_LEAD` before its window), quiet hours, mutes, `REPEAT_HOLD`, Offline, and the notification's text. No network, no clock of its own.
- `alert_store.py`: the phones' alerts in SQLite (`alerts.sqlite` beside the history store, or `ALERTS_FILE`; owner-only): each phone's push subscription, mutes, quiet hours, what was sent to it and the items waiting for a baseline (a phone just added, an item just unmuted: the next check records what is due for them, unsent). A phone added again with the same push address keeps its row, with a new id and secret.
- `alert_push.py`: sends one Web Push message using the VAPID keys in `.env`: `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`. Returns `ok`, `gone` for an expired subscription, or `error`.
- `alerts.py`: the routes under `/api/alerts` and the checker thread: every `ALERT_CHECK_SECONDS` it reads the same Summary the page shows through the app's caches and sends what `alert_rules` says is due, leaving out the actions read from an Offline monitor. It runs where Home Air reads the home (a token, or the monitors on the Wi-Fi) with push keys; phones are added only with remote access over HTTPS.
- `static/alerts.js`: the Alerts bell in the header, its own round button left of the Home menu (`#alerts-btn` in `.bar`), and its panel: on a phone a sheet from the bottom that follows the finger (turn on, mute an action, quiet hours, a test, the phones), on a computer a small panel with the QR code; `sw.js` shows notifications.
- `agent_tools.py`: what an AI assistant gets: the two read-only tools (`home_air_now`, `home_air_history`), their descriptions and their answers (no coordinates, serials or tokens). `mcp_server.py` serves them at `/mcp` (MCP over HTTP, one JSON answer per POST, Origin-checked) and at `/api/agent/now` and `/api/agent/history` for the skill's script.
- `skills/home-air-quality/`: the skill for coding agents on the same computer: it reads the home and, for a change, sends the agent to "Change your home" above (`SKILL.md`, and `scripts/home_air.py`, standard library only, which asks `/api/agent/now` and `/api/agent/history`). `setup.py --agent on` (`./homeair skill add`; `--url` saves another Home Air's address in `agent-url`, which the script reads) links it into the agent's skills folders (`~/.agents/skills`, and `~/.claude/skills` for Claude Code), never over another skill, and records the links in `agent-links.json` so `--agent off` removes only those. `docs/agents.md` ("Connect your AI assistant") covers every other agent and app.
- `config.py`: reads `config.toml` (or the `CONFIG_TOML` or `CONFIG_FILE` variable) and checks it. `config.example.toml` is the commented starter.
- `index.html` and `static/`: the page, with no build step. `app.js` (data, readings, charts), `summary.js` (the summary card), `home.js` (the home's profiles and road in words), `sky.js` (the live sky), `map.js` (the neighborhood map), `picker.js` (the demo's top area, its three ways in and the monitor picker).
- Serving: the page's `/static/` URLs carry `?v=<content hash>` (`server.py` `version_assets`); a URL with the current hash is cached by browsers for a year (`immutable`), anything else revalidates. `sw.js` and `/manifest.json` are never immutable. Text answers are gzipped in the app (`compress`) for gunicorn and Docker; on Vercel its edge compresses and serves `/static/`. gunicorn stays at 1 worker: the server cache and the daily call limits live in that one process.
- `DESIGN.md`: the page's design rules (cards, disabled states, colors that carry meaning, motion, the check before shipping); read it before changing the page.
- `publicmap.py`: AirGradient's public monitors for any home or the demo: the ones in an area of its public map (one small request), the nearest, the outdoor source an indoor-only home is offered at setup, a monitor's reading row, its place on the public map and its history there. No import of `demo/` or `server.py`.
- `demo/`: the demo's home (`config.toml`), its made-up data (`data.py`), its three moments and three hidden one-monitor cases (`moments.py`) and the public monitors' pages at `?monitor=<id>` (`public.py`).
- `roads.py`: geometry helpers for the roads near a home: `local_xy` (metres east and north of a point), `seg_distance`, `bearing_deg` (0 = north, clockwise) and `haversine_m`. Standard library only.
- `road_data.py`: the road data on this computer, in `road-data/` beside `config.toml` (or `ROAD_DATA_DIR`; owner-only): `index.json` and the selected metro or regional file from `ROADS_BASE` (`HOMEAIR_ROADS_BASE` changes it), both capped in size. The current default targets GitHub's `data` release, which is not published. When setup looks up roads for a saved place (not a bare `./homeair setup` or a profile-only change) and saved roads aren't kept, Home Air fetches a fresh index during setup even when one is cached. It downloads an area's file only when a matching index entry supplies a sha256 and no cached file matches. `setup_roads` writes the roads found at the saved coordinates to `[[roads]]` with `found = "auto"`, a backup first, and leaves every other `[[roads]]` block as it is. A failed download leaves `pending`; Home Air retries at start only with a config file, outside hosted environments and containers, and when that marker makes a retry due (at most once a day). Separately, at start on this computer, a server or Railway, Home Air downloads missing files named by saved automatic roads, plus the index if a trusted digest is missing. These downloads need a trusted sha256 from the index or retained digests (no sha256, no download). Home Air keeps the saved filename; an app-version change does not select newer data. `route_stretch` is the map's route: a road's lines within 3 km of the rounded place (`/api/road/route?i=`); without the file the roads are named, not drawn.
- `scripts/roads/`: the public build of metro and regional road files from an OpenStreetMap extract (`python3 scripts/roads/build.py <metro> <input.osm.pbf> --out DIR`; needs `osmium`). `metros.toml` lists the metros (box, extract URL, level), `names/<metro>.toml` is a metro's reviewed name table (which name to show for a piece, and whether it takes "the"). It keeps motorway, trunk and primary roads and their ramps, simplifies to 5 m, groups the pieces into roads, and writes `<metro>-<yyyy-mm>.json.gz` and `index.json` under ODbL 1.0 with the OpenStreetMap credit. `tests/test_roads_build.py` runs on `tests/fixtures/roads/tiny.geojsonseq`, without osmium. The release policy uses versions (`data-v1`, …), with publication when rules or data improve plus a yearly map refresh; each Home Air version must name its data version. Dates in filenames do not set an update schedule.
- `scripts/`: `settings.py` (backup, restore and show `config.toml` for "Change your home"), `setup.py` (prepares an empty token entry, discovers monitors, writes household choices with a backup, checks config and readings; `railway` transfers saved settings through stdin, `--live` checks a running page and `--agent` adds the skill for the coding agents on this computer), `check_config.py`, `screenshot.sh` (the README's images, from the demo), `vercel_build.sh` (the demo's build on Vercel).
- `homeair`: the short commands (`./homeair help` lists them). Each one runs a script in `scripts/` with its flags, through `scripts/run.sh`; no logic of its own, and the long forms still work. Docs and messages name only the short forms.
- `Dockerfile`, `docker-compose.yml` and `.dockerignore`: the container (DEPLOYMENT.md, "Docker"). `.github/workflows/image.yml` publishes the image to `ghcr.io/sguyon/homeair` from `main`, and to the old name `ghcr.io/sguyon/home-air-quality` so existing installs keep updating.
- `tests/`: Python tests (pytest) and page tests (`*.test.mjs`, Node's test runner).

## Run

```bash
uv run --with-requirements requirements.txt server.py
```

Then http://localhost:5555 (`PORT` changes the port). `scripts/run.sh server.py` does the same and finds or installs uv first (else Python 3.11+ in `.venv`), as `./homeair setup` does. Without uv: a virtualenv with `pip install -r requirements.txt`, then `python server.py`. Python 3.11 or newer. `DEMO=1` runs the demo whatever the config.

## Test

```bash
pytest -q -n auto --dist loadgroup && node --test tests/*.test.mjs
```

Both must pass before a commit. While iterating, run only the tests the change touches (`pytest tests/test_x.py -x -q`). `-n auto` needs `pytest-xdist` (in `requirements-dev.txt`). A bare `pytest` runs `tests/` only (`pytest.ini`).

## Commits

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `type(scope): summary`.

- `type`: `feat` (something new for the owner), `fix`, `docs`, `refactor`, `test`, `chore` (build, tools, dependencies).
- `scope`: the part of Home Air (`alerts`, `summary`, `setup`, `sky`, `agent`, `readme`).
- `summary`: lowercase, no full stop, under 72 characters, what changes for the owner.
- Body, when needed: at most 3 short lines on why. No list of every change: the diff, the docs and the tests have them.
- A change that breaks an existing install (a setting renamed, a command removed) gets `!` after the scope and a `BREAKING CHANGE:` line.

## The demo moments are the test fixtures

The demo's three moments (`demo/moments.py`: `rush`, `clear`, `humid`, at `?moment=`) are made-up data tuned so the rules tell each story, and `tests/test_demo.py` pins what each one says. A change to the page or the rules that changes a story updates the moments and their tests in the same commit, so the demo keeps showing what the app does. Three hidden moments (`indoor-near`, `indoor-model`, `outdoor-only`) cover homes with one monitor and are pinned the same way; they're reachable only at `?moment=`, not in the top area's samples. `scripts/screenshot.sh` takes the README's images from the demo.

## Numbers

- A threshold a household would tune lives in `config.toml`: `[limits]`, and `[home] appliances` for what dries a damp room. Its default is in `config.py` (`DEFAULT_LIMITS`, `APPLIANCES`), and `config.example.toml` says what it does.
- Every other tuning number is a named constant at the top of its module, with a one-line comment: what it is, its unit, and why that value.
- Never put an unnamed threshold in the middle of a function. Keyword arguments that name themselves (`ttl=600`, `timeout=15`) are fine.
- The same in `static/*.js`: a number that means one of the config's limits reads `APP.limits` (or a helper in `home.js` that takes it), never a copy. A display scale that isn't a limit (an AQI band edge, a ring's full scale) is a named constant at the top of its file with a comment.

## Copy

- Plain and factual, no marketing.
- Things don't do what people do: a skill isn't "connected", a copy doesn't "know", a page doesn't "turn into" anything. Say what the person or the program does ("`./homeair setup` adds the skill", "set `HOME_AIR_URL`").
- Name the object instead of "it" or "this" when either could mean two things ("run the command again", not "run it again" after a line about the app).
- Say what will be done and what the person gets ("The Home Air skill is now in Claude Code: ask it about the air inside"), not only what happened.
- Name where Home Air runs, with the words the sentence depends on: where it can be reached from, "with remote access" (from anywhere: alerts, claude.ai and ChatGPT, who sees the page); where it runs, "on this computer", "on a home server", "on a hosted server" (not on the home's Wi-Fi: the token, the history) or "on another computer" (any other machine, a home server included); "on Railway" only for Railway's own steps. Never "Home Air online" or "a copy".
- Setup is a step, not someone: write "during setup", or name the command (`./homeair setup`). In a message to the user, say the result ("The Home Air skill is now in Claude Code").
- Simple English, for readers whose first language isn't English: no idioms ("stuffy", "may well be").
- No stream of "I" in the messages an agent says: say the result or name the person instead ("The Home Air skill is now in Claude Code", "Your settings change only when you ask").
- Docs, code and comments describe what exists now: no plans, "next" or "later" (those live in the maintainer's roadmap). A claim about another product (what an assistant can reach, a command's syntax) is checked against its docs and linked, or left out.
- The Summary's items are called **actions**: the same word on the page, in the docs and in the agent tools (`read.actions`). Each says what to do, when, and why for this home, never a general tip. In plain sentences about what Home Air does, everyday words are fine ("what to do", "advice"), as long as they don't rename the items.
- **Profiles** are ready-made types of person a home picks from (toddler, sinus & allergy, runner): `[profiles]` in `config.toml`. Say "the profiles that match your home"; when saying how the air suits them, talk about the person ("whether the air suits each of them"), not the profile. Never call them "needs" (the old name).
