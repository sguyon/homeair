# <img src="static/icons/icon.svg" alt="" width="28" height="28"> Home Air

An app for your AirGradient monitors that tells you what to do about your air: when to open the window, when to keep it shut, when to go for a run. It combines your readings with the forecast, pollen, wildfire smoke and the public monitors around you.

**[Try the demo](https://homeair.app)** (an example home, or any public AirGradient monitor) · [Installation](#installation) · [Features](#features) · [How it works](#how-it-works)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/hero-dark.png">
    <img src="docs/hero-light.png" alt="Home Air's page next to its phone view" width="800">
  </picture>
</p>

<sub>Screenshots from the demo, with sample data.</sub>

## Installation

You need one or two AirGradient monitors (indoor, outdoor or both). Home Air reads the ones on your computer's Wi-Fi directly, one reading a minute, with no account and no token. An AirGradient API token is optional: it adds the last 7 days of history and is needed to deploy Home Air online. Works best in the US.

**With a coding agent** (Claude Code or Codex). Open your agent anywhere and paste:

```
Set up Home Air for my home by following the instructions here: https://raw.githubusercontent.com/sguyon/homeair/main/SETUP.md
```

Home Air goes in a `homeair` folder; to choose another folder, name it in the prompt.

The agent finds your monitors on the Wi-Fi and opens your page, then asks a few questions about your home, most of them optional. You can keep Home Air on your computer, or put it on a home server or online later ([DEPLOYMENT.md](DEPLOYMENT.md)).

**AI assistants:** ask Claude, ChatGPT and other agents about the air at home, and they answer from your monitors. Coding agents use Home Air's skill, other assistants its MCP server ([how](docs/agents.md)); claude.ai and ChatGPT need Home Air online.

**With Docker,** on a home server or NAS: [Docker setup](DEPLOYMENT.md#docker).

**Just trying it?** Without settings or a token, Home Air opens the demo: `uv run --with-requirements requirements.txt server.py`, then http://localhost:5555.

## Features

- **Actions.** For CO₂ and humidity inside, PM2.5 and pollen outside: "Open a window for 10 min", "Keep windows shut", each with one line on why.
- **Timing.** The actions look ahead, so you get "Windows shut until 11 AM" or "Run after 11 AM", from a PM2.5 forecast scaled to your outdoor monitor (without one, to the public monitors near you when there are some).
- **Profiles.** Pick the profiles that match your home (a toddler, someone with sinus allergies, a runner), and the page says whether the air suits each of them right now.
- Charts of today with its forecast, and of each monitor's history (1 hour to 7 days)
- Weather, pollen, wildfire smoke, and a nearby highway's exhaust when the wind blows it your way
- Public AirGradient monitors nearby, to see whether bad air is regional or just around your home
- A live sky behind the page, with the real sun, clouds, rain and haze. Useless, but my toddler's favorite feature :)
- Installs on your phone's home screen like an app (a PWA): Share › Add to Home Screen (iPhone, Safari) or ⋮ › Install app (Android, Chrome); it works offline only on https, which Home Air online (on Railway) has

## Why I built it

I have two AirGradient monitors at home, one inside and one outside. AirGradient's dashboard shows their readings well, but it doesn't say what to do about them, and the rest of what matters lives in other apps: the weather, the pollen season, rush hour on the expressway a kilometer away. I wanted one place that says what to do about the air at home, for what my household cares about: a toddler's sleep, sinus allergies, and running.

My first version had an AI model write the actions, but it kept suggesting the same few things, so I turned each one into a plain rule. I plan to try AI again for patterns across weeks of readings.

It's minimal on purpose, and by necessity: I built it around what my own two monitors measure :) It's meant to be easy to extend with your coding agent: the rules are short Python functions with their numbers named at the top, and [AGENTS.md](AGENTS.md) tells an agent how the code fits together. Adding a profile of your own, like someone who cooks on gas, or a baby ([how to add one](docs/optional.md#adding-a-profile)), hasn't been tried by anyone else yet: if you do, tell me how it went in an issue, or send a pull request.

## Limitations

- **Works best in the US.** Wildfire smoke (NOAA) and the forecast and pollen links use US sources. Elsewhere, turn those off in `config.toml`: your monitors' readings, the actions, the weather and the PM2.5 forecast still work.
- **One or two monitors.** An indoor one, an outdoor one, or both. With an indoor monitor only, outdoor PM2.5 comes from a nearby public monitor or an estimate for your area; with an outdoor monitor only, the toddler profile waits for an indoor one. No rooms or several monitors of the same kind yet.
- **Wi-Fi reads need the same network.** Home Air finds monitors by browsing the local network (mDNS), which doesn't work inside Docker or WSL, and Home Air online (on Railway) can't reach your Wi-Fi at all. A home server reads the addresses found during setup; Home Air online reads AirGradient's cloud with your token, up to 5 minutes old. Monitors need firmware 3.1.4 or newer.
- **No road for new homes yet.** The road and its exhaust need a route file traced along the road, and nothing in the repo builds one yet. The demo has one; new homes start without.

## Data and privacy

- **Your readings stay on your computer.** When Home Air reads your monitors on the Wi-Fi (on your computer or a home server), it keeps their readings in its own history: one row a minute per monitor in `history.sqlite` next to `config.toml` (the `/data` volume with Docker), readable by its owner only, kept until you delete it and never sent anywhere. Each monitor's last known address is kept beside it in `local-addresses.json`.
- **Home Air online keeps no history of its own:** its charts come from AirGradient's cloud, held in memory for a day at most.
- **No accounts, no analytics in Home Air.** The server's logs list which sources it called, never readings or keys. Only the public demo site counts page views and loading speed (Vercel Web Analytics and Speed Insights: no cookies, nothing personal); Home Air on your computer, a home server or online has none.
- **What leaves your server:** your AirGradient API token, when you add one, goes only to AirGradient. Reading a monitor on the Wi-Fi sends nothing outside your network. Your home's exact position stays in `config.toml`; rounded to about 1 km by the app, it goes to Open-Meteo (weather and forecast) and to Google if you add a pollen key.
- **Calls to AirGradient:** with every monitor read on the Wi-Fi, the app makes no call for current readings. How often it asks AirGradient otherwise, and how it keeps that low, is in [docs/api-usage.md](docs/api-usage.md).

## How it works

The actions come from fixed rules in `summary.py` and `server.py`, not a model. Each rule compares a reading with a limit from your `config.toml` (`[limits]`). The defaults, and why:

| Limit | Default | Why |
|---|---|---|
| CO₂ inside | 800 ppm | About twice the outdoor level (around 420). Above it the room doesn't get enough fresh air, and people start to notice; guidelines put the line between 800 and 1,000. |
| PM2.5 outside | 15 µg/m³ | The World Health Organization's guideline for a 24-hour average. Used here for any hour, so it's strict on purpose. |
| PM2.5 outside, high | 35 µg/m³ | The US EPA's 24-hour standard. Above it, sensitive people (children, asthma) should keep time outside short. |
| Humidity inside | 30–60%, comfortable up to 50% | Under 30% dries the nose and throat; over 60% helps mold and dust mites grow. The damp-room action stops at 55%. |
| Pollen | 3 of 5 | "Moderate" on Google's pollen index, where people with allergies start to feel it. |

Change any of them in `config.toml`; the comments in [`config.example.toml`](config.example.toml) say what each one does.

How the PM2.5 forecast is fitted to your monitor, and which corrected PM2.5 values the rules use: [docs/how-it-works.md](docs/how-it-works.md).

## Screenshots

<p align="center">
  <img src="docs/screenshot.png" alt="The full Home Air page from the demo: today's actions, the profiles, the day chart, the home monitors, local conditions and the neighborhood map" width="720">
</p>

## Credits

- Your monitors, and the nearby public monitors: [AirGradient](https://www.airgradient.com) ([API](https://api.airgradient.com/public/docs/api/v1/), [public map](https://www.airgradient.com/map/)). Nearby monitors' data is licensed under [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); each monitor's owner is named in its row on the page.
- Weather and the PM2.5 forecast: [Open-Meteo](https://open-meteo.com) ([Weather API](https://open-meteo.com/en/docs), [Air Quality API](https://open-meteo.com/en/docs/air-quality-api)).
- Pollen: [Google Pollen API](https://developers.google.com/maps/documentation/pollen) (optional, needs a key).
- Wildfire smoke: NOAA's [Hazard Mapping System](https://www.ospo.noaa.gov/products/land/hms.html).
- Map: [MapLibre](https://maplibre.org), with tiles from [OpenFreeMap](https://openfreemap.org) and map data from [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
- Device frame in the link-preview images: [Devices.css](https://github.com/picturepan2/devices.css) (MIT, `scripts/vendor/`).

## Contributing

Questions, bugs and ideas are welcome as GitHub issues. Never paste a token or key in an issue or pull request, even a partial one. To report a security problem, see [`SECURITY.md`](SECURITY.md).

Pull requests: install `requirements-dev.txt` (`pip install -r requirements-dev.txt`), run `pytest -q` and `node --test tests/*.test.mjs` first, and keep your `config.toml`, `.env` and readings out of the diff. Agents working on the code read [`AGENTS.md`](AGENTS.md).

## License

[MIT](LICENSE)
