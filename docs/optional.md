# Optional settings

These can wait until your monitors show readings. Agents run the commands they can; ask before each change to a live account or service. Never read or print .env, or ask for a key in chat.

## Pollen

Without a Google key, pollen is off and the sinus & allergy profile uses humidity alone. Add `GOOGLE_POLLEN_API_KEY` to `.env` in your editor and restart, or transfer it to Railway with `./homeair railway --service <name>` after approval. Set `[sources] pollen = false` to turn it off entirely.

The key comes from Google's Pollen API. It needs a Google Cloud project with billing turned on: 5–10 minutes, most of it creating the account and adding a payment card. Do every step you can with `gcloud` so the user only signs in and adds the card. Google gives 5,000 calls a month free, then charges $10 per 1,000 (Google's price list, checked November 2026). The app keeps each day's pollen until midnight, so each running copy (your computer, a server) makes about one call a day, plus one after each restart or deploy, and one when the page's Refresh button is pressed more than 10 minutes after the last. The setup below caps the calls at 150 a day and limits the key to the Pollen API, so a bug or a leaked key can't run up a bill.

**With `gcloud`** (the agent runs the commands, except the two the user does):

1. The user signs in, in Claude Code by typing: `! gcloud auth login`. In another agent, in their own terminal.
2. Pick a project: an existing one (`gcloud projects list`), or a new one (`gcloud projects create <id>`). Then `gcloud config set project <id>`.
3. Billing: `gcloud billing accounts list`. If there's an account, link it: `gcloud billing projects link <id> --billing-account=<account id>`. If there's none, the user adds one in the Cloud console (it asks for a card): https://console.cloud.google.com/billing.
4. Enable the Pollen API and the API Keys API (`gcloud services api-keys` needs it), create a key limited to the Pollen API, and write it into `.env` without printing it:

   ```bash
   gcloud services enable pollen.googleapis.com apikeys.googleapis.com
   gcloud services api-keys create --key-id=home-air-pollen --display-name="Home Air pollen" \
     --api-target=service=pollen.googleapis.com >/dev/null 2>&1
   gcloud services api-keys describe home-air-pollen --format='value(displayName)'
   K="$(gcloud services api-keys get-key-string home-air-pollen --format='value(keyString)')" \
     && [ -n "$K" ] && printf '\nGOOGLE_POLLEN_API_KEY=%s\n' "$K" >> .env; unset K
   ```

   The output of `create` and `get-key-string` contains the key. Never run either of them again without the redirect or the `K="$(…)"` capture above, or the key lands in the chat. `describe` confirms the key exists without showing it.

   If `.env` already has a `GOOGLE_POLLEN_API_KEY` line, remove it first without printing anything: `{ grep -v '^GOOGLE_POLLEN_API_KEY=' .env || true; } > .env.tmp && mv .env.tmp .env`.
5. Cap the calls: at most 150 pollen forecasts a day (under 5,000 a month, the free allowance), and no heatmap calls (the app doesn't use them). The quota commands are in gcloud's beta group: `gcloud components install beta` if it's missing.

   ```bash
   gcloud beta quotas preferences create --project=<id> --service=pollen.googleapis.com \
     --quota-id=ForecastQueriesPerDay --preferred-value=150 --preference-id=pollen-forecast-daily-cap \
     --allow-high-percentage-quota-decrease
   gcloud beta quotas preferences create --project=<id> --service=pollen.googleapis.com \
     --quota-id=HeatMapQueriesPerDay --preferred-value=0 --preference-id=pollen-heatmap-off \
     --allow-high-percentage-quota-decrease
   gcloud beta quotas info list --service=pollen.googleapis.com --project=<id> --format='table(quotaId,dimensionsInfos[0].details.value)'
   ```

   The last command shows the limits: 150 for `ForecastQueriesPerDay`, 0 for `HeatMapQueriesPerDay`. When a day's 150 are used, further calls are refused until the next day. A quota is a call limit; a budget alert only sends a warning. If the project already used more than 150 pollen calls in a recent day, Google refuses the cap; add `--allow-quota-decrease-below-usage` to the first command, and tell the user, since something called pollen far more than the app does.

**Without `gcloud`**, give the user these steps in the Cloud console (https://console.cloud.google.com):

1. Pick or create a project, and make sure billing is on.
2. APIs & Services → Library → "Pollen API" → Enable.
3. APIs & Services → Credentials → Create credentials → API key. Edit the key: under API restrictions, restrict it to the Pollen API.
4. Copy the key into `.env` as `GOOGLE_POLLEN_API_KEY=<key>`: they open `.env` in an editor and add the line (replacing one that's already there).
5. Cap the calls: APIs & Services → Pollen API → Quotas & System Limits. Set the forecast's per-day limit ("Pollen - Forecast Usage", per day) to 150 and the heatmap's per-day limit ("Pollen - HeatMap Usage", per day) to 0.

Also create a billing budget alert in the project's Billing → Budgets & alerts, with an amount the user chooses and email alerts enabled. An alert does not stop spending. Check restrictions on an existing key too: `gcloud services api-keys list` and `gcloud services api-keys describe <key-id>`; its API target must be `pollen.googleapis.com`. Verify the quota with the command above.

The optional `[[pollen_seasons]]` blocks in `config.example.toml` show typical northeastern US dates. Copy them only if they fit the home; elsewhere use local typical dates and say where they came from. They are a calendar, not a forecast.

## Alerts on your phone

Phone alerts use Web Push, which needs Home Air at an HTTPS address: on Railway ([DEPLOYMENT.md](../DEPLOYMENT.md#railway)) or on a home server behind HTTPS. Without the push keys (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` in `.env`), the Alerts panel says "Set up alerts first: the alert keys are missing.", and nothing is sent.

- Keys: `./homeair setup` writes them to `.env`; `./homeair railway --service <name>` transfers them after approval.
- Railway: a volume at `/data` keeps `alerts.sqlite` (each phone's subscription and choices) across deploys ([DEPLOYMENT.md](../DEPLOYMENT.md#railway)).
- iPhone: iOS 16.4 or later, with Home Air added to the Home Screen from Safari and opened from that icon (Web Push works only for Home Screen web apps). Android: Chrome.
- Checks run every 2 minutes through the page's caches: with a token, Home Air reads the cloud as if the page were open all day ([api-usage.md](api-usage.md)).

Privacy: alerts reach the phone through its push service, encrypted so the service can't read them ([how](https://web.dev/articles/push-notifications-web-push-protocol)). `alerts.sqlite` keeps each phone's push address and choices, and no readings.

## The road

`./homeair setup` finds main roads automatically where road data is built. **Your home → Around you** lists every road found, on and off. Switches work only at a local address on the computer running Home Air; they are read-only anywhere else (on a hosted server or in a container). Switches allow at most four roads on, counting a hand-set road. Turning every automatic road off stops following those roads; a hand-set road remains followed.

For Home Air on a hosted server, ask a coding agent to change that road’s `on` setting in `[[roads]]`, following [Change your home](../AGENTS.md#change-your-home) and [After changing config.toml](../AGENTS.md#after-changing-configtoml). The upload command rounds the place, converts automatic-road arcs to eight-point sectors and re-sends saved keys. Updating Home Air on a hosted server needs approval, as for other settings.

US data covers the 50 largest Census metros and the rest of the 50 states in regional files, including Alaska and Hawaii; DC is included. Only the Counted metros use Census boxes; Measured NYC uses its separately configured core. Core west and south edges are included, east and north edges excluded. Longitude wraps to `[-180°, 180°)`, with +180° treated as -180°. A place counts as covered when it lies in a 2° cell that touches one of the 50 states or DC, even where that cell has no road file. These cells can include water and foreign land along borders. With no configured roads or pending download, the page says "Traffic · road data isn't built for your area yet" only if it can read that saved setup outcome; otherwise it says "Traffic · no main road within 1 km".

Source downloads belong to the build: the builder supplies Geofabrik's OpenStreetMap extract and the pinned BTS NTAD HPMS 2024 file. When setup looks up roads for a saved place (not a bare `./homeair setup` or a profile-only change), Home Air fetches a fresh index from `ROADS_BASE` (`HOMEAIR_ROADS_BASE` overrides it), even when one is cached, unless it keeps the saved roads. It downloads an area's file only when a matching index entry supplies a sha256 and no cached file matches. The data release is [`data-v1`](https://github.com/sguyon/homeair/releases/tag/data-v1). The road-data release policy uses versions (`data-v1`, …), with publication when rules or data improve plus a yearly map refresh; each Home Air version must name its data version. Home Air does not refresh saved roads on an app-version change. See [data and license](roads.md#data-and-license) for the current download target.

In the 25 November 2026 build, half the regional files are under about 60 KB; the largest is about 1.6 MB, compressed. Home Air uses the cached file during normal operation. At start, Home Air retries a failed setup download only with a config file, outside hosted environments and containers, and when a `pending` marker makes a retry due (at most once a day). Separately, on this computer, a server or Railway, Home Air downloads missing files named by saved automatic roads, requesting the index too if a trusted digest is missing. A restart on Railway can need these downloads. Home Air keeps the saved file name rather than selecting a newer file.

NYC uses measured weekday slow hours where available. Other US roads use estimated busy hours: 6 AM to 10 PM for highways, 7 AM to 8 PM for primary roads, on weekdays and weekends. These defaults are based on one Brooklyn expressway's count profile, the BQE. Where matched, a road's displayed count comes from one HPMS stretch for the whole road group in that file, not necessarily the stretch near the home. Counts and truck shares do not change timing, strength or actions.

The build joins same-name pieces only when an end of one lies within 125 m of another piece. On numbered motorways and trunks, route numbers replace local street names and structural names, unless a reviewed name overrides the rule. NYC's reviewed name table turns the rule off for all of NYC. Distinct highway names stay; unnamed and route-only pieces use their first route number. Reviewed name overrides are optional.

A hand-set `[road]` still works with its saved `name`, `route` and `rush`; automatic setup does not overwrite it. `toward` is optional: Home Air can compute the arc from the saved route.

During setup, one lookup sends the typed address to OpenStreetMap's Nominatim. With `--lat` and `--lon` instead of an address, Nominatim receives those coordinates rounded to four decimal places to look up the place name. Home Air saves the exact place in `config.toml` on this computer. During setup, Home Air sends the place rounded to two decimal places (about 1 km) to Open-Meteo for the timezone when needed. When Home Air finds no outdoor monitor of the home's own (only an indoor monitor or no monitor found), it requests a widened box around the rounded place from AirGradient's public map to select an outdoor source. On this computer, Home Air filters those monitors to a 16 km box around the exact place and ranks them by distance from the exact place. During setup's config check, AirGradient can also receive a second box around the rounded place and, if the selected public monitor is absent from that box, its ID for place and current-reading requests.

`./homeair check --suggest` is separate from setup. When checking an indoor-only home's saved outdoor source, the command asks AirGradient's public map for an area around the rounded place. Otherwise, `--suggest` uses the coordinates as saved in `config.toml`, which may be exact, for that area request. The command may also request the saved public monitor by its id. Home Air does not run `check --suggest` during setup.

Road-data requests go to the host configured by `ROADS_BASE` (`HOMEAIR_ROADS_BASE` overrides it). When setup looks up roads for a saved place and doesn't keep the saved roads, they fetch a fresh `index.json`; an area's file, for example `nyc-2026-11.json.gz`, is requested only when a matching index entry supplies a sha256 and no cached file matches. The file name reveals the selected area: BBBike's NYC box, a Census metro box or a 2° region (or a smaller split). The configured host receives the requested file name and the requesting IP address; a host reached through a download redirect receives the redirect URL and the requesting IP address. The default host is GitHub, whose release downloads can redirect to `release-assets.githubusercontent.com` ([GitHub privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)). Home Air sends no home coordinates in these road-data requests. After setup, the place sent for weather, pollen and the map is rounded to about 1 km. The page receives the rounded place and the saved pollen ZIP when set; opening its pollen.com link sends that ZIP to pollen.com. `./homeair railway` rounds the place when `config.toml` is sent off this computer.

The page gets road names, a short key made from the road's name, distance bands, compass sides, counts and truck shares where known, and whether roads reach now or lie on several sides. Agents get names, bands, sides and whether roads reach now on relevant actions, without home coordinates. These details narrow where the home is.

Automatic-road arcs become eight-point sectors when `config.toml` is sent off this computer (`./homeair railway`), and their map routes are cut within 3 km of the rounded place. A hand-set `[road]` keeps its own degree arc there and on the page, with a distance estimate; its map route is shown as saved.

Roads **© OpenStreetMap contributors**, ODbL 1.0; counts from FHWA HPMS; speed profiles credit NYC DOT, NYC Open Data. See [Roads near your home](roads.md) for the sources, license, timing, privacy, coverage, and US build and check commands (`build.py us`, `freeze_us_clip.py`, `check_us.py`). The check reruns the fixed points and rewrites the metro reports.

## Adding a profile

A profile is code, not a setting. To add one, touch each of these and keep the tests green (`pytest -q && node --test tests/*.test.mjs`):

- `config.py`: add it to `PROFILES`.
- `summary.py`: its state in `profiles_row`, and which items serve it.
- `air_read.py`: its line in `build_profiles`.
- `phrases.py`: its sentences, in `PILLS`.
- `static/home.js`, `static/app.js`, `static/summary.js`: its name, icon and wording.
- `index.html`: its chip under "Profiles" in the Home menu.
- `config.example.toml`: a commented line under `[profiles]`.
- `demo/` and its tests: the demo moments are the test fixtures, so a new profile shows up in them in the same commit.

## The demo on Vercel

The public demo runs on Vercel (`vercel.json`), with no keys. `DEMO=1` is set in the Vercel project's environment variables, not in `vercel.json`: a new Vercel project needs it added there. Live data isn't tested there: the server refreshes your monitors' readings in the background, and that hasn't been tried on Vercel's functions.

