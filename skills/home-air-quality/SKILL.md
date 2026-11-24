---
name: home-air-quality
description: Home Air: the air quality in your home, from the user's own AirGradient monitors and outdoor data (weather, pollen, smoke, traffic), with what to do and when: open or close the windows, air a room, run a purifier or a dehumidifier. Use when the user asks about the air in their own home, or wants to change their Home Air settings. Questions it answers: is the air OK inside, should I open the window, is the CO2 too high, is it too damp or too dry, is there smoke outside, can I go for a run, how was the air last night or this week. Changes it covers: who lives here ("my dad has asthma", "I started running", a toddler), a stricter or looser CO2, PM2.5 or humidity limit, a new dehumidifier, a new address, Fahrenheit or Celsius. Needs Home Air running for this home; changes need its folder on this computer. Not for the air in other places, weather forecasts, medical questions, or keys and tokens.
license: MIT
compatibility: Needs Python 3 (standard library only) and a running Home Air server for the home, at HOME_AIR_URL (default the port chosen during Home Air's setup, else http://localhost:5555). Reading is read-only. Changes edit only config.toml, with a backup, following the Home Air folder's AGENTS.md.
metadata:
  author: Sacha Guyon
  version: "0.1"
  homepage: https://homeair.app
---

# Home Air

Home Air reads one home's AirGradient monitors and outdoor data, and lists what to do and when, each with its reason. This skill gets those readings and actions, so your answer is about this home, not air in general. Add what you know about the person: who is asking, who lives there, what they said earlier.

## Server address

`HOME_AIR_URL` if set; else, when this skill is linked from a Home Air folder (as during setup), the port Home Air was started on, then `http://localhost:5555` if that one doesn't answer; else `http://localhost:5555`. Every call below is read-only.

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

`now` returns `readings` (indoor, outdoor; each with `at`, the time in UTC, `minutes_ago`, and `stale`: true when it was taken more than 30 minutes ago), `home.limits` (this home's thresholds) and `read`. `home.demo: true` means it's Home Air's demo with made-up readings, not this person's home: say so first.

In `read`:

- `read.actions`: the ranked things to do. Each has `action` (what), `when` (timing, in the home's clock), `value`, `vs_limit` and `why`. The first one matters most.
- `read.all_clear: true`: nothing to do right now.
- `read.profiles`: how the air suits each person type the home chose (toddler, sinus and allergy, runner), with a state and a why.
- `read.status`: `good`, `warn` or `bad`, the worst of the actions.
- Units: PM2.5 in ug/m3, CO2 in ppm, humidity %, temperature in Celsius (convert if the user thinks in Fahrenheit; `home.temp_unit` is what the page shows).

`history` returns per metric `latest`, `min`, `max`, `max_at` and `hourly_mean`, times in the home's local time with its UTC offset, up to `until`. Compare with `limits` (CO2 `co2`, PM2.5 `pm`, humidity `rh_damp` and `rh_dry`).

## How to answer

1. Call `now` first for any "should I" question; add `history` only for questions about the past.
2. Home Air's actions (`read.actions`) are its rules applied to the readings and this home's limits: use them as the starting answer, then think it through with the readings, the history and what you know about the person (who is asking, who lives there, what they told you).
3. Give one answer. If it differs from Home Air's action, give the reason for the difference in the same sentence ("Open it at noon: the exhaust clears at 11 AM, but the baby naps until noon"), since Home Air's card may show its own timing beside your words.
4. Use only readings the server returns; never invent one. A cause Home Air didn't name is a guess and is said as one ("maybe the cooking"). If a field is `null` or a metric is missing, say that monitor does not report it.
5. Lead with the answer and its timing in one sentence that stands alone ("Yes, open it for about 10 minutes, after 11 PM"): it may be all that a notification, a voice reply or a hurried reader gets. Then the reasons in two or three plain sentences with rounded numbers, in the server's words (`why`) where they fit. End with `read.source_line` as it is ("Bedroom monitor, 9:10 PM"): which monitor and when. Never leave it out.
   - Plain sentences: no tables, boxes, columns or emoji that carry meaning, so the answer reads the same in a terminal, on a phone, in a messaging app or aloud. Bold, if any, only on the first sentence.
   - Two to four sentences, about 60 words, unless asked for more. No list, even for several numbers.
6. A "should I" about one room: the monitors are per home, not per room. Say which monitor the number comes from.
7. If a reading is stale (`stale: true`), start by saying when it was taken: "the last reading is from 9:10 PM".
8. This is information about the air, not medical advice. For symptoms, point to a clinician.

## Changing the home

For a change ("add a runner", "make the CO₂ line stricter", "we got a dehumidifier", "switch to Fahrenheit", "we moved"): go to the Home Air folder and follow its `AGENTS.md`, section "Change your home". The folder is `HOME_AIR_DIR` if set; else, when this skill is linked from a Home Air folder (as during setup), the folder two levels above this skill's real path (`realpath` of this folder, then `../..`); else ask. That section is the one source for the steps: plan and wait for a yes, back up, edit `config.toml`, check, restart, report with the undo. A request it can't cover (a new kind of person, the page's look, the rules) is a code change: say so.

## Removing the skill

When the user asks you to remove the Home Air skill (or to undo what was added during setup), run `./homeair skill remove` in the Home Air folder, found as for changes. The command removes only the links made during setup; say what it printed. `./homeair skill status` shows the links.

## When the script fails

The script prints `{"error": "..."}` and exits 1.

- "not answering": either the server is stopped, or your commands run in a sandbox without network access (Codex's default sandbox blocks even localhost). If you are sandboxed, run the command again with network access (ask to run it outside the sandbox; in Codex, `network_access = true` under `[sandbox_workspace_write]` in `~/.codex/config.toml` allows it for good) before saying the server is down. Never guess the air from outdoor knowledge, and never answer from a reading seen earlier in this conversation: say the script failed and why. Offer to start Home Air (`./homeair start` in the Home Air folder) only if the user asks.
- "No Home Air answers … the skill's files aren't in a Home Air folder": the skill was installed on its own. Say that the skill reads a running Home Air, and pass on the error's steps: start the user's Home Air, set HOME_AIR_URL to its address, or install Home Air from the link in the error.
- `python3` itself fails to start, for example "couldn't create cache file '/tmp/xcrun_db…' (Operation not permitted)" on a Mac: that's your sandbox, not Home Air. Ask to run the command outside the sandbox.
- "older than the skill": the running Home Air predates these routes. Say so and give the update step the error names.
- "something else answers": that port is another program, not Home Air. Check `HOME_AIR_URL` or the port chosen during setup.
- "no AirGradient token yet": setup is unfinished; point to SETUP.md in the Home Air repo.
- Any other error: AirGradient or a data source is unavailable; say what the error says and try once more later.

Never ask the user for a token and never read their `.env`. Touch `config.toml` only through the "Change your home" steps.
