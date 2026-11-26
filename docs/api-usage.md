# How Home Air uses AirGradient's API

## What it calls

| Endpoint | For | How often, at most |
|---|---|---|
| Monitor `GET http://<monitor's address>/measures/current` (no token; `ag_local`) | Each monitor on your Wi-Fi, read directly; nothing leaves your network | Every 60 seconds per monitor, whenever the app runs, whether a page is open or not |
| API `GET /locations/measures/current` (home's token) | The home's monitors now, all in one request: only when a monitor is not read on the Wi-Fi (a home read through the cloud, or a monitor that stopped answering) | Every 5 minutes, only while someone has the app open |
| API `GET /locations/{id}/measures/past` (home's token) | History charts and the day's summary for a monitor not read on the Wi-Fi; and the 7-day fill of the home's own history (one call per monitor, covering the gaps in the last 7 days) | The last 24 hours once a day per monitor, then only readings newer than the ones held, at most every 5 minutes. The 7-day range: once an hour |
| Map `GET /measurements/current/area` (no token) | Nearby monitors and the "area" estimate: one box 16 km each way, about 6 KB. The public demo's place picker: 50 km, about 9 KB | Every 15 minutes per place, only while the app is open with Nearby or the area estimate on, or when a demo visitor opens the picker or searches |
| Map `GET /locations/{id}` and `/locations/{id}/measures/current` (no token) | A home without an outdoor monitor reads one public monitor chosen at setup: name and position (about 450 B), reading (about 200 B) | Place once a day; reading every 5 minutes while the app is open |
| Map `GET /locations/{id}/measures/history` (no token) | That monitor's history (for the area estimate: PM2.5 of the 5 nearest) | Every 5 minutes, when a chart or the summary is shown |

"Map" is `map-data.airgradient.com/map/api/v1`, the backend of [AirGradient's open-source map](https://github.com/airgradienthq/airgradient-map). The public API can return all public monitors in the world or one by id, but it has no "near a place" query, so the app uses the same area search the map uses. Setup makes one area call to list the monitors near the home. After that the app reads only the one monitor it chose. If you'd prefer another route, we'll switch.

## How the calls stay low

- **Shared cache.** The browser only talks to the app's server, and the server keeps one cached answer per source. Ten open tabs make the calls one tab makes. With no tab open, the server makes no calls to AirGradient's cloud or the other outside sources; the one background job is the poller that reads your own monitors on the Wi-Fi.
- **Read on the Wi-Fi first.** For each monitor the app uses its Wi-Fi reading when it is under 90 seconds old, then the cloud's reading when there's a token, then the last stored reading marked "not answering". When every monitor answers on the Wi-Fi, there are no calls for current readings at all.
- **The 7-day fill.** With a token, the gaps longer than 10 minutes in the last 7 days of the home's own history are filled from the cloud: at start, when a token is saved, and when reads resume after more than 10 minutes. One `measures/past` call per monitor, at most 7 days at start; after that, a fill asks only for the time since the last one (less 10 minutes), so a gap the cloud has nothing for isn't asked again. Rows already stored are never replaced.
- **The page.** A tab on screen re-checks the server every 5 minutes, when it's shown again, and when someone presses Refresh. The server's cache answers. Refresh reaches AirGradient at most once a minute, and the area search not at all before its 15 minutes are up. A hidden tab never asks.
- **Why 5 minutes.** AirGradient stores past readings in 5-minute buckets. The interval can be changed with `AIRGRADIENT_REFRESH_SECONDS` (60 at least).
- **History in small steps.** Each monitor's last day stays in memory and only new readings are fetched. The 1-hour, 6-hour and 24-hour charts all come from that one day.
- **One call at a time.** Requests that need the same data at the same moment share one call.

## Being a good client

- **User-Agent** on every outside call: `HomeAir/<version>`, the version being the release on the page's build line.
- **Retry-After.** A 429 or 503 stops every call to that host for as long as `Retry-After` says (15 minutes without it, 6 hours at most). A 429 also writes an alert line to the log.
- **Backoff.** Other failures (a 5xx, a timeout, no network) pause that source 2 minutes, then 4, 8, 16, up to 30 while it keeps failing. One good answer resets it. A refused token (401) waits 30 minutes.
- **Daily caps** ([`budgets.py`](../budgets.py)), far above normal use, as a safety stop. Past a cap the server stops calling that source until the next UTC day and logs one line.

| Source | Calls a day, at most |
|---|---|
| API, current readings (token) | 5,000 |
| API, history (token) | 10,000 |
| Monitors on the Wi-Fi | 6,000 |
| Map, area search | 2,000 |
| Map, one monitor | 8,000 |

- **`GET /api/usage`** on any Home Air shows today's calls per source, the caps, paused sources and 429 counts. No personal data.

## Calls per day for one home with two monitors, read through the cloud

A home read on the Wi-Fi makes none of these: its two monitors make 2,880 reads a day on your own network, counted as `ag_local`. The table is for Home Air on a hosted server (Railway, for example), or a home read only through the cloud.

| Use | Token calls | Map calls with Nearby on |
|---|---|---|
| Nobody has it open (with no phone's alerts on) | 0 | 0 |
| Opened about 10 times | about 50 | at most 10 area searches (about 60 KB) |
| On screen all day | at most about 900 | at most 96 area searches (about 560 KB) |

With phone alerts on, Home Air reads the cloud as if the page were on screen all day, even when nobody opens it: it checks every 2 minutes, through the same 5-minute cache.

A thousand homes that each open the app 10 times a day make about 35 token calls a minute, plus about 7 area searches a minute with Nearby on.

Want a different interval, lower caps, another endpoint or header? [Open an issue](https://github.com/sguyon/homeair/issues) and we'll change it.
