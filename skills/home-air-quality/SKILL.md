---
name: home-air-quality
description: Use this skill when the user asks about the air in their own home, or wants to change their Home Air settings. Home Air is an open-source app that reads the user's AirGradient air monitors and outdoor data (weather, pollen, smoke, traffic) and says what to do and when: open or close the windows, air a room, run a purifier or a dehumidifier. Questions it answers: is the air OK inside, should I open the window, is the CO2 too high, is it too damp or too dry, is there smoke outside, can I go for a run, how was the air last night or this week. Changes it makes: who lives here ("my dad has asthma", "I started running", a toddler), a stricter or looser CO2, PM2.5 or humidity limit, a new dehumidifier, a new address, Fahrenheit or Celsius. Needs Home Air running for this home; changes need its folder on this computer. Not for the air in other places, weather forecasts, medical questions, or keys and tokens.
license: MIT
compatibility: Needs Python 3 (standard library only) and a running Home Air server for the home, at HOME_AIR_URL (default the port Home Air setup chose, else http://localhost:5555). Reading is read-only. Changes edit only config.toml, with a backup, following the Home Air folder's AGENTS.md.
metadata:
  author: Sacha Guyon
  version: "0.4"
  homepage: https://homeair.app
---

# Home Air

Home Air reads one home's AirGradient monitors and outdoor data, and lists what to do and when, each with its reason. This skill gets those readings and actions, so your answer is about this home, not air in general. Add what you know about the person: who is asking, who lives there, what they said earlier.

## Server address

`HOME_AIR_URL` if set; else, when this skill is linked from a Home Air folder (setup does that), the port setup started it on, then `http://localhost:5555` if that one doesn't answer; else `http://localhost:5555`. Every call below is read-only.

## Ask the server

One call for "now" (current readings plus the air read), stdlib Python, run from this skill's folder:

```bash
python3 -I scripts/home_air.py now
```

For "how has it been" (hourly means in the home's own timezone, default 12 h, up to 168):

```bash
python3 -I scripts/home_air.py history --hours 12 --where indoor     # or --where outdoor
```

Without Python, the same by hand (the answers are the same as Home Air's MCP tools at `/mcp`):

```bash
curl -s "${HOME_AIR_URL:-http://localhost:5555}/api/agent/now"
curl -s "${HOME_AIR_URL:-http://localhost:5555}/api/agent/history?hours=12&where=indoor"
```

## Reading the answer

`now` returns `readings` (indoor, outdoor), `home.limits` (this home's thresholds) and `read`:

- `read.actions`: the ranked things to do. Each has `action` (what), `when` (timing, in the home's clock), `value`, `vs_limit` and `why`. The first one matters most.
- `read.all_clear: true`: nothing to do right now.
- `read.profiles`: how the air suits each person type the home chose (toddler, sinus and allergy, runner), with a state and a why.
- `read.status`: `good`, `warn` or `bad`, the worst of the actions.
- Units: PM2.5 in ug/m3, CO2 in ppm, humidity %, temperature in Celsius (convert if the user thinks in Fahrenheit; `home.temp_unit` is what the page shows).

`history` returns per metric `latest`, `min`, `max`, `max_at` and `hourly_mean`, times in the home's local time with its UTC offset, up to `until`. Compare with `limits` (CO2 `co2`, PM2.5 `pm`, humidity `rh_damp` and `rh_dry`).

## How to answer

1. Call `now` first for any "should I" question; add `history` only for questions about the past.
2. Lead with the answer in a first sentence that stands alone ("Yes, open it for about 10 minutes, after 11 PM"): it may be all a notification, a voice reply or a hurried reader gets. Then the why from `why` in two or three plain sentences, in the server's words where they fit, with rounded numbers. End with the monitor and the time (`readings.*.at` is UTC).
   - Plain sentences only: no tables, boxes, columns or emoji that carry meaning, so it reads the same in a terminal, a phone, a messaging app or aloud. Bold, if any, only on the first sentence.
   - Under about 60 words unless asked for more.
3. A "should I" about one room: the monitors are per home, not per room. Say which monitor the number comes from.
4. Stay inside what the server returned. Never invent a reading, a limit, a time or a cause. If a field is `null` or a metric is missing, say that monitor does not report it.
5. If the readings are old (`at` more than about 30 minutes before now), say so before advising.
6. This is environmental information, not medical advice. For symptoms, point to a clinician.

## Changing the home

For a change ("add a runner", "make the CO₂ line stricter", "we got a dehumidifier", "switch to Fahrenheit", "we moved"): go to the Home Air folder and follow its `AGENTS.md`, section "Change your home". The folder is `HOME_AIR_DIR` if set; else, when this skill is linked from a Home Air folder (setup does that), the folder two levels above this skill's real path (`realpath` of this folder, then `../..`); else ask. That section is the one source for the steps: plan and wait for a yes, back up, edit `config.toml`, check, restart, report with the undo. A request it can't cover (a new kind of person, the page's look, the rules) is a code change: say so.

## Removing the skill

When the user asks you to remove the Home Air skill (or to undo what setup added), run `scripts/run.sh scripts/setup.py --agent off` in the Home Air folder, found as for changes. It removes only the links setup made; say what it printed. `--agent status` says what's linked.

## When it fails

The script prints `{"error": "..."}` and exits 1.

- "not answering": either the server is stopped, or your commands run in a sandbox without network access (Codex's default sandbox blocks even localhost). If you are sandboxed, run the command again with network access (ask to run it outside the sandbox; in Codex, `network_access = true` under `[sandbox_workspace_write]` in `~/.codex/config.toml` allows it for good) before saying the server is down. Never guess the air from outdoor knowledge. Offer to start it (`scripts/run.sh scripts/setup.py --start` in the Home Air folder) only if the user asks.
- "older than the skill": the running Home Air predates these routes. Say so and give the update step the error names.
- "something else answers": that port is another program, not Home Air. Check `HOME_AIR_URL` or the port setup chose.
- "no AirGradient token yet": setup is unfinished; point to SETUP.md in the Home Air repo.
- Any other error: AirGradient or a data source is unavailable; say what the error says and try once more later.

Never ask the user for a token and never read their `.env`. Touch `config.toml` only through the "Change your home" steps.
