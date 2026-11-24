# Setup

**Done when** `./homeair setup` ends with `Live OK` or "Nothing is read yet" (no monitor found; not a failure), and the user has answered or skipped step 4.

For a coding agent setting up Home Air for someone's home. Run the commands yourself. Steps 1 to 3 ask nothing. Don't ask about the home before `./homeair setup` has run. Don't change the app's code. Never ask for a key in chat, read or print `.env`, or commit private settings. Ask before changing a live service or account.

One question per message, in plain words; wait for the answer. With a question tool (Claude Code's AskUserQuestion), ask every question with it, the default first, except where step 4 says. Before the next question, say what you did with the answer. Don't ask what the user already said.

## 1. Get Home Air

Tested on Mac and Linux; on Windows, use WSL or Docker. In a cloud sandbox (Claude Code on the web, Codex cloud), say the app can't stay there and to run setup on their own computer, then stop. Developing in a sandbox is fine (AGENTS.md, "Run").

Run this in the folder you were opened in, or the one the user names:

```bash
linked=$(cd -P ~/.agents/skills/home-air-quality/../.. 2>/dev/null && pwd)
if [ -x "$linked/homeair" ]; then dir=$linked                                   # already set up here: use that folder
elif [ -x ./homeair ] && [ -f SETUP.md ]; then dir=.                           # this is Home Air's folder
elif git rev-parse --is-inside-work-tree >/dev/null 2>&1; then dir=~/homeair   # inside another project: keep Home Air out of it
elif [ -z "$(ls -A)" ]; then dir=.                                             # an empty folder made for it
else dir=./homeair; fi
[ -x "$dir/homeair" ] || git clone https://github.com/sguyon/homeair "$dir"
cd "$dir" && pwd
```

Tell the user the printed folder, with: "Home Air is set up on this computer first. After that, you can keep Home Air on this computer, or run it on a home server or online, with the same settings." If the folder is `~/homeair` because you were inside another project, say so in one line.

Run every command below from this folder. `./homeair` runs them through `scripts/run.sh` with uv (installed if missing), else Python 3.11 or newer, and says which, or what to install. Some commands wait: `./homeair connect --wait` up to 90 s, `start` 30 s, `start-at-login on` 60 s on a Mac, `check` 10 s after a start. Use a timeout of at least 100 s; don't poll.

## 2. Run the setup command

If your commands run in a sandbox (Codex's default blocks the network and writing outside the opened folder, and asks before either: [Codex docs](https://learn.chatgpt.com/docs/agent-approvals-security)), ask the user once to let you run Home Air's commands outside it.

```bash
./homeair setup
```

About a minute: at most 3 s for the monitors, 30 s for the app, 60 s for macOS's prompt. Use a timeout of at least 150 s; don't poll. On a Mac, say as you run it: "macOS will say "homeair" can run in the background, and ask to allow "uv" to find devices on local networks: "uv" is the program Home Air runs through. Choose Allow."

It reads each AirGradient monitor on the Wi-Fi once, writes `config.toml` (serials, default names, units; no place or profiles), starts the app now and at each login, opens the page (on the token form when no monitor answered), and checks the page and each reading. It doesn't add the skill; step 4 asks.

Its lines:

- `indoor: found on your Wi-Fi, read directly`: read on the Wi-Fi once a minute. One monitor per kind is read: the one in `config.toml`, else the lowest serial. `Also found: … To read it instead: ./homeair monitors use <serial>`: run it if the user wants that one (it checks the monitor, saves with a backup, restarts the app). Firmware older than 3.1.4, or a model setup doesn't read: not read; tell the user.
- `Settings: <path>`: the file written. An existing `config.toml` is first copied to `config.toml.bak-<time>`; the household's choices are kept.
- `Started: http://localhost:5555`: the page's address (from port 5600 up if 5555 is taken). Use the address it prints.
- `Nothing is read yet` (after "No AirGradient monitor answered on this network"): an ordinary home; the token is step 1, required here. The user doesn't see your output: send this, then end your turn:

  > **Home Air is running at http://localhost:5555**
  >
  > `./homeair setup` opened the token page in your browser. No AirGradient monitor answered on your Wi-Fi. Home Air can read your monitors through AirGradient's Cloud API instead, with your AirGradient API token.
  >
  > **Step 1 of 4: Your AirGradient API token**
  >
  > *Required. Without the token, Home Air has no readings to show.*
  >
  > Follow the steps on the token page (about 1 min). You type the token on that page, not in this chat. When the page says Saved, reply **done** here.

  On their reply, run `./homeair connect --wait` (it prints the account's monitors, never the token); on "Not saved yet", say so in one line and wait for done again. Then send step 3's message with "Saved. The token is in `.env` in Home Air's folder. Your AirGradient account has <n> monitors, and the page now shows <them>:" before the readings, and "Step 1 is done. Next come 3 optional steps (about 2 min). To stop at any step and do the rest later, reply **skip**." as its last line, then go on to step 4's address. A refused token or an account with no monitor: pass on what the page says. No monitor at all: the user can set one up with AirGradient's instructions; meanwhile [the demo](https://homeair.app) shows any public monitor ("Near me").
- `WSL can't find the monitors on your Wi-Fi` (also with `a container` or `Home Air online`): `./homeair setup` can't find the monitors from there. WSL: use the token path above. Docker: `./homeair home-server` (step 5). Cloud sandbox: step 1.
- `Waiting for the first reading on your Wi-Fi … press Allow`: Home Air started at login waits for macOS's prompt "Allow "uv" to find devices on local networks?". `No reading on your Wi-Fi after 60 s`: tell the user to turn on "uv" in System Settings › Privacy & Security › Local Network, then run `./homeair check`.
- `Start at login isn't available here (…)`: the app stops when the computer restarts; say so. `./homeair start` starts it again.
- `Problem:` or `Not live yet`: fix what the line says, then run the command again (safe to repeat).

With the token, Home Air matches monitors by serial and fills past readings; a Wi-Fi monitor not in the account (a neighbour's) is replaced by the account's monitor of that kind.

## 3. Tell the user it's ready

Send this, with the real address and readings (`python3 -I skills/home-air-quality/scripts/home_air.py now`; the skill needn't be loaded). One line per monitor: name, kind, the reading nearest its limit, unit and limit (the home's own limits, from that output); over a limit, Home Air's first action instead ("**Open a window for 10 minutes:** CO₂ is 1,100 ppm, over the 800 ppm limit."). Leave out a missing reading. Never "Live OK" or a check's name.

> **Home Air is running at http://localhost:5555**
>
> `./homeair setup` opened the page in your browser.
>
> Both monitors are read on your Wi-Fi:
> - **Living room** (indoor): CO₂ is 650 ppm, under the 800 ppm limit.
> - **Balcony** (outdoor): PM2.5 is 4 µg/m³, under the 15 µg/m³ limit.
>
> Home Air is in your Mac's Login Items, so it starts again each time you turn on this Mac and sign in.
>
> Next come 4 optional steps (about 4 min). To stop at any step and do the rest later, reply **skip**.

Adjust: on Linux, "It starts again each time you turn on this computer and sign in."; no login sentence after "Start at login isn't available here". Setup run again: skip the steps already done (`./homeair status`) and say how many are left. Then step 4, in the same turn.

## 4. Your home: four optional steps

Each step is one message, sent as written below: a bold title "Step N of 4: …", a blank line, one italic line saying optional (or required) and why, the facts, then the question and the exact reply. After each answer, say in one line what was saved and what the page now shows. With the question tool, the default goes first so Enter keeps it; the address and the profiles are always plain text (Claude Code's box adds its own "Type something" and "Chat about this" rows; Codex's picks one). Reached from step 3, or later when the user says "set up my home in Home Air": skip what's done (`./homeair status` lists what's left).

**skip**, at any step: send "**No more questions for now.** Your settings stay as they are. To answer them later, open Claude Code or Codex in <Home Air's folder> and say: set up my home in Home Air." and stop.

1. **The token** (skip when `./homeair status` says it's added):

   > **Step 1 of 4: Your AirGradient API token**
   >
   > *Optional on this computer. Required to deploy Home Air online.*
   >
   > Home Air reads your monitors on your Wi-Fi, through their Local API. On the Wi-Fi, a monitor gives only its current reading, so the charts show readings from now on.
   >
   > With the token, Home Air also reads your monitors through AirGradient's Cloud API, and the charts also show the readings from before setup.
   >
   > Adding the token takes about 1 min, on a page in your browser. You type the token on that page, not in this chat. [How AirGradient's APIs work](https://www.airgradient.com/documentation/kb/where-do-i-access-the-api-documentation-api-token-and-local-api/#how-the-local-api-works)

   Question tool: "Add your AirGradient API token now?", options "Yes" ("Opens the token page. The charts then show readings from before setup.") and "Not now" ("To add it later: ./homeair connect"). Plain text: "Add the token now? Reply **yes** to open the token page, or **no** to go to step 2 (to add the token later: `./homeair connect`)."

   On yes: run `./homeair connect` and open the address it prints (`open` on Mac, `xdg-open` on Linux); send "The token page is open in your browser: http://localhost:5555/connect. Follow the steps on that page. When the page says Saved, reply **done** here." and end your turn. On done: `./homeair connect --wait`, then "**Saved.** The token is in `.env` in Home Air's folder. The charts now show readings from before setup." Can't find the token: "Show me where", under the page's own step 1, opens a marked picture of AirGradient's settings (General Settings, Connectivity, API Access); walk them through it in words. The page can't open: the user types the token into `.env` (`open -e .env` on Mac, `xdg-open .env` on Linux), then `./homeair start`; `./homeair check` says whether the token is set, never its value.

2. **The address** (plain text):

   > **Step 2 of 4: Your address**
   >
   > *Optional. Without an address, the page has no weather and no nearby public monitors.*
   >
   > Home Air uses the address for the weather, the nearby public monitors and the timezone.
   >
   > Your exact address stays in `config.toml` on this computer. What's sent for weather and pollen, and what the page shows, is rounded to about 1 km.
   >
   > You can type an exact address, like *1475 Broadway, New York*, or a neighborhood or landmark, like *Times Square, New York*.
   >
   > **What is your home's address?** Type it as you would in a map app.

   Run `./homeair setup --address "<their answer>"`, then `./homeair start`, and send "**Saved:** <the place setup printed>. The page now shows the weather there and the nearby public monitors." Never invent a location. Not found: retry with what the user gave (a ZIP code, a nearby street or landmark) before asking again. Several matches: ask which city or state, then run the command again. Other fixes: `--name`, `--lat` with `--lon`, `--timezone`. No token needed; the monitors found on the Wi-Fi stay.

3. **Who lives here** (plain text, in every agent):

   > **Step 3 of 4: Who lives here**
   >
   > *Optional. Without an answer, the actions are for a general home.*
   >
   > The page suggests actions, like closing a window or running a purifier. To personalize them for your home, which of these apply?
   >
   > 1. Young child (baby, toddler)
   > 2. Sinus trouble or seasonal allergies
   > 3. Running or sport outside
   >
   > Type the numbers, like **1 3**. If none apply, type **none**.

   1 is `toddler`, 2 `sinus`, 3 `runner`: run `./homeair setup --profiles <list or none>`, then `./homeair start`, and send "**Saved:** <the people, in the list's words>. The page now says whether the air suits each of them."

4. **The skill** (skip when `./homeair skill status` says it's linked; that command also names the coding agents found):

   > **Step 4 of 4: Ask your AI agents about the air**
   >
   > *Optional. The page works the same without the skill.*
   >
   > Home Air comes with two ways for AI agents to answer from your readings:
   > - **The Home Air skill**, for the coding agents on this computer: <the agents found>. In any folder, you can ask them "How is the air inside now?". They change your settings only when you ask and say yes.
   > - **The MCP server**, at http://localhost:5555/mcp. It is part of Home Air, so there is nothing to install. To use it in another assistant: [docs/agents.md](docs/agents.md)

   Question tool: "Add the Home Air skill to <the agents found>?", options "Yes" ("Links the skill into each agent's skills folder.") and "Not now" ("To add it later: ./homeair skill add"). Plain text: "Add the Home Air skill to <the agents found>? Reply **yes**, or **no** (to add the skill later: `./homeair skill add`)." On yes: `./homeair skill add`, then "**Done.** The Home Air skill is now in <the agents it names>. Open a new session in one of them and ask, for example: "How is the air at home? Should I open a window before bed?"" and, as its own paragraph, "To remove the skill: `./homeair skill remove`, or ask me."

Then the end, in one message:

> **Setup is complete.**
>
> *Optional:* the page shows pollen once you add a Google Pollen API key to `.env`. How to get a key (5 to 10 min, with a Google Cloud account that has billing on): [docs/optional.md](docs/optional.md#pollen)
>
> **Next: deploy Home Air (optional)**
>
> Home Air runs on this computer. The page opens only here, and only while this computer is on. To open the page from your other devices, deploy Home Air to a home server or online. `./homeair home-server` and `./homeair railway` copy your settings and keys from this computer (`config.toml` and `.env`), so you don't run setup again there.

Then ask where, as step 5 says.

## 5. On a home server or online

With the question tool, "Only this computer" goes first (the default):

> Where should Home Air run?
> 1. **Only this computer:** nothing more to do. You can close this window.
> 2. **A home server, with Docker:** reads your monitors on your Wi-Fi. The page opens on your home network only.
> 3. **Online, on Railway:** the page opens from anywhere, with no sign-in. Anyone with its address sees your readings. Needs your AirGradient API token.
> 4. **Another host that runs Docker** (Fly.io, Render, a rented server): they can build Home Air from its Dockerfile. Not tested on these hosts.
>
> Reply 1, 2, 3 or 4.

Also when the user asks later ("put Home Air online"). Before deploying, ask only what `./homeair status` lists as missing, one line each: the address (step 4's step 2) and, for online, the token ("Home Air online reads your monitors through AirGradient's Cloud API, so it needs your AirGradient API token", then step 4's step 1).

- A home server: run `./homeair home-server` and pass on the two steps it prints. Don't offer to copy the folder over SSH; do more only if asked. Once Home Air runs there, if you can reach the server, run `./homeair check http://<the server>:8000`.
- Online: [DEPLOYMENT.md](DEPLOYMENT.md#railway), with its one OK before anything changes on Railway. The location sent is rounded to about 1 km.
- Another host: the [Dockerfile](Dockerfile) and the variables DEPLOYMENT.md lists for Railway; not covered here.

Afterwards: "Home Air online is at <the address>. Home Air on this computer still runs and starts at login. To turn it off: `./homeair stop` and `./homeair start-at-login off`." When the skill is added, run `./homeair skill add --url <the address>` and add "The Home Air skill now reads Home Air online." Other AI assistants: [docs/agents.md](docs/agents.md).

## Commands for later

`./homeair help` lists the commands: `./homeair start` starts or restarts Home Air, `./homeair start-at-login on` (or `off`) starts it at each login (on a Mac, or Linux with systemd), and `./homeair status` shows what's done and what's left. To change the settings by hand, edit `config.toml` ([config.example.toml](config.example.toml)), then run `./homeair start`; keep a private copy of it, since it isn't in git. A missing reading: check that the monitor is online and set as indoor or outdoor in AirGradient. Optional extras: [pollen, road tracing, new profiles, the Vercel demo](docs/optional.md). To open the page from a phone at home: `HOST=0.0.0.0 ./homeair start-at-login on` (anyone on that Wi-Fi can then open it), then `http://<the computer's name>.local:5555` on the phone; to add it to the home screen: [On your phone](README.md#on-your-phone).
