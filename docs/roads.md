# Roads near your home

During setup, Home Air finds the main roads near the home. The Summary shows an action such as "Keep the east windows shut" when wind or calm morning air brings exhaust from a followed road.

**Your home → Around you** lists every road found, on and off, with its name, distance band and side. Switches work only at a local address on the computer running Home Air; they are read-only anywhere else (on a hosted server or in a container). Switches allow at most four roads on (`ROADS_MAX`), counting a hand-set road. Turning every automatic road off stops following those roads; a hand-set `[road]` remains followed. For Home Air on a hosted server, ask a coding agent to follow the [settings steps](../AGENTS.md#change-your-home).

The demo uses the same automatic lookup at its rounded Brooklyn place. Its generated `[[roads]]` are in
[`demo/config.toml`](../demo/config.toml), with the command to regenerate them using
[`scripts/roads/demo.py`](../scripts/roads/demo.py) and a checked NYC release file. A small clip in `demo/road-data/`
keeps those roads' geometry, counts and measured slow hours, with the OpenStreetMap credit and ODbL licence.
The clip's sha256 is checked before drawing routes. The demo uses the packaged clip on this computer and on Vercel;
the sample pages never download road data or read a home's road cache. The made-up morning forecast turns the wind
away from the BQE at 11 AM; the BQE's measured weekday slow hours continue until 10 PM.

## Data and license

The build uses [OpenStreetMap](https://www.openstreetmap.org/copyright) through [Geofabrik's US extract](https://download.geofabrik.de/north-america/us/index.html) and [BBBike's NYC extract](https://download.bbbike.org/osm/bbbike/NewYork/), federal [HPMS 2024 traffic counts distributed by BTS (NTAD)](https://geodata.bts.gov/datasets/5e6a977c2d7c4ec1bdc82e684d3384f2/about) (2.6 GB), and [NYC DOT speeds on NYC Open Data](https://data.cityofnewyork.us/Transportation/DOT-Traffic-Speeds/i4gi-tjb9). These sources are downloaded for the build: the builder supplies the extract and the pinned national HPMS zip, and the build fetches speeds. During household setup, Home Air fetches a fresh index from the data host, even when one is cached, unless it keeps the saved roads. It downloads an area's file only when a matching index entry supplies a sha256 and no cached file matches. It never asks a traffic service about your home.

The publication target for the gzipped metro and regional files and `index.json` is [Home Air's GitHub releases](https://github.com/sguyon/homeair/releases). The first road-data release is [`data-v1`](https://github.com/sguyon/homeair/releases/tag/data-v1). The files are a derivative database under [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), with the public [build script](../scripts/roads/build.py): **© OpenStreetMap contributors**. HPMS is US public domain, but files that include HPMS counts are still under ODbL. Speeds credit **NYC DOT, NYC Open Data**.

The release policy uses versions (`data-v1`, `data-v2`, …): publish when the rules or data improve, plus a yearly map refresh. Each Home Air version must name its road-data version. A date in a file name records the build year and month; it is not an update schedule.

The downloader uses `ROADS_BASE` in `road_data.py`, overridden by `HOMEAIR_ROADS_BASE`. Its default is `https://github.com/sguyon/homeair/releases/download/data-v1`, the road-data release this Home Air version reads. Home Air does not select a data release by version or refresh saved roads when the app's version changes.

## How roads are picked

Home Air searches within 1 km for OpenStreetMap motorway, trunk and primary roads (`FIND_M`), leaving out ramps and tunnels. The build joins same-name pieces only when an end of one lies within 125 m of another piece; distant roads with the same name stay separate. On numbered motorways and trunks, a local street name or a structural name (such as a bridge, ramp or upper level) uses the route number instead, unless a [reviewed name](../scripts/roads/names/) overrides that rule. NYC's reviewed name table turns this rule off for all of NYC, so NYC roads keep their reviewed or OpenStreetMap names. For example, "Main Street" on a numbered highway can become "I-95", "US-1" or "NJ-139". A distinct highway name, such as "Harbor Freeway", stays. Unnamed and route-only pieces use their first route number and join nearby pieces with that number. Each region works without a reviewed name table.

Roads are taken in this order:

1. Motorway or trunk at most 500 m away (`REACH_M`), including exactly 500 m.
2. Primary road at most 200 m away (`PRIMARY_MAX_M`).
3. Motorway or trunk beyond 500 m, up to 1 km, on calm mornings only.
4. Primary road beyond 200 m, up to 1 km: listed off, but can be switched on.

Inside each group, the nearer road comes first, then motorway before trunk before primary, then name A to Z. Home Air follows up to four roads from groups 1–3 by default. It saves up to six roads (`LIST_MAX`), including off rows, for **Around you**.

Distance bands are "within 200 m", "within 500 m" and "500 m to 1 km". The side is the eight-point compass direction to the nearest point on the road.

Points every 20 m along each road give two wind ranges: the road within 500 m, and on calm mornings, up to 2 km (`CALM_REACH_M`). A range wider than 180° (`ARC_CAP`) accepts every wind direction for that range. The page says "on several sides" when the nearer range touches all eight compass sectors.

## What "busy" means

"Busy" names a time window, taken from a hand-set schedule, measured slow hours or defaults. The traffic count and truck share are shown as context; neither changes the timing, strength or actions. Where matched, a road group in a file gets one HPMS stretch's count for the whole road, not necessarily the stretch near the home.

At the same flow, slower traffic holds more vehicles on each stretch (density = flow / speed). Home Air uses slow hours as an estimate of stop-and-go traffic. [Barth & Boriboonsomsin (2008)](https://doi.org/10.3141/2058-20) study CO₂ only. [FHWA's MOBILE6.2 example](https://www.fhwa.dot.gov/ENVIRONMENT/air_quality/conformity/research/mpe_benefits/mpe02.cfm) models NOx from 2006 light-duty vehicles on arterials, with emissions falling as speed rises in that example. EPA now uses [MOVES](https://www.epa.gov/moves), which replaced MOBILE6.2. Neither source measures traffic's NO₂ or ultrafine particles at the home, and Home Air does not calculate emissions from these sources.

NYC profiles use four weeks of weekday hourly medians for each NYC DOT link. From 5 AM to before 10 PM, an hour below half that link's own fastest hourly median across all 24 hours is slow. During setup, Home Air combines slow windows from links within 1 km of the home (`FIND_M`), in either direction; with no nearby link, it uses whole-road windows where available. These are typical hours, not live traffic. With no reading in seven days, the build can reuse slow hours from the previous file with `--previous DIR`.

Without measured hours, US defaults are 6 AM to 10 PM for highways (motorway and trunk) and 7 AM to 8 PM for primary roads, in the home's timezone. These are estimates based on one Brooklyn expressway's count profile, the BQE, not measurements across US roads. Counted roads use these defaults on weekdays and weekends. Outside the US, defaults are 7 to 10 AM and 4 to 8 PM. Weekends use the defaults for the road type. A hand-set `rush` applies every day and takes priority.

## Wind, distance and calm mornings

During busy hours, exhaust "reaches you now" when the wind comes from the road's nearer range at 7 km/h or more (`CALM_KMH`). Below 7 km/h, wind must come from the wider range during the two hours before sunrise, ending at sunrise (`CALM_BEFORE_SUNRISE_H`); busy hours are not required. Both rules need wind direction and speed; only calm reach needs solar data, the next real sunrise. Busy reach works without it, including polar days and nights. The window uses two elapsed hours across clock changes and can start the previous evening. Polar days and nights with no sunrise have no calm window. [Hu et al. (2009), summarized by UCLA](https://www.uclahealth.org/news/release/air-pollution-from-freeway-extends-further-than-previously-thought), measured the long reach 1–2 hours before sunrise in light winds, with concentrations back to background within about 300 m after sunrise. The 7 km/h cutoff is an unverified choice.

[Karner et al. (2010)](https://doi.org/10.1021/es100008x) found that most pollutants approached background levels over hundreds of metres; PM2.5 mass showed no consistent trend with distance. [HEI Special Report 17](https://www.healtheffects.org/system/files/SR17Traffic%20ReviewPressRelease011310.pdf) identifies a 300 to 500 m zone affecting 30 to 45% of people in large North American cities. These findings inform the rule. They do not measure the air at your home.

The action names the windows on the sides of the roads reaching now and ends at the first forecast wind change, end of busy hours or sunrise within 12 hours when none of those roads reaches the home; a road that starts reaching later gets its own action then. An end at sunrise is rounded to the quarter hour ("until about 6:45 AM"). Calm reach ends at sunrise even without hourly wind data. Busy reach follows its own hours and wind; sunrise does not change it. A forecast hour’s wind is used only from that hour onward. Without a known end time, it says "while the air stays calm before sunrise" for calm mornings, or names the wind direction. A short airing for CO₂ or humidity takes priority. The PM2.5 monitor does not measure traffic's NO₂ or ultrafine particles.

## Privacy

During setup, one lookup sends the typed address to OpenStreetMap's Nominatim. With `--lat` and `--lon` instead of an address, Nominatim receives those coordinates rounded to four decimal places to look up the place name. Home Air saves the exact place in `config.toml` on this computer. During setup, Home Air sends the place rounded to two decimal places (about 1 km) to Open-Meteo for the timezone when needed. When Home Air finds no outdoor monitor of the home's own (only an indoor monitor or no monitor found), it requests a widened box around the rounded place from AirGradient's public map to select an outdoor source. On this computer, Home Air filters those monitors to a 16 km box around the exact place and ranks them by distance from the exact place. During setup's config check, AirGradient can also receive a second box around the rounded place and, if the selected public monitor is absent from that box, its ID for place and current-reading requests.

`./homeair check --suggest` is separate from setup. When checking an indoor-only home's saved outdoor source, the command asks AirGradient's public map for an area around the rounded place. Otherwise, `--suggest` uses the coordinates as saved in `config.toml`, which may be exact, for that area request. The command may also request the saved public monitor by its id. Home Air does not run `check --suggest` during setup.

Road-data requests go to the host configured by `ROADS_BASE` (`HOMEAIR_ROADS_BASE` overrides it). When setup looks up roads for a saved place and doesn't keep the saved roads, they fetch a fresh `index.json`; an area's file, for example `nyc-2026-11.json.gz`, is requested only when a matching index entry supplies a sha256 and no cached file matches. The file name reveals the selected area: BBBike's NYC box, a Census metro box or a 2° region (or a smaller split). The configured host receives the requested file name and the requesting IP address; a host reached through a download redirect receives the redirect URL and the requesting IP address. The default host is GitHub, whose release downloads can redirect to `release-assets.githubusercontent.com` ([GitHub privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)). Home Air sends no home coordinates in these road-data requests. Automatic roads are found from the precise place saved in local `config.toml`.

After setup, the place sent for weather, pollen and the map is rounded to about 1 km. The page receives the rounded place and the saved pollen ZIP when set; opening its pollen.com link sends that ZIP to pollen.com. `./homeair railway` rounds the place when `config.toml` is sent off this computer. The page gets road names, a short key made from the road's name, distance bands, compass sides, counts and truck shares where known, and whether roads reach now or lie on several sides. Agents get names, bands, sides and whether roads reach now on relevant actions, without home coordinates. Road details narrow where the home is.

`./homeair railway --service <service>` sends automatic-road arcs as eight-point sectors. Automatic map routes are cut within 3 km of the rounded place (`ROUTE_CLIP_M`). A hand-set `[road]` is an exception: its own arc in degrees is kept as the owner drew it, when `config.toml` is sent off this computer and on the page, with a distance estimate. Its map route is shown as saved.

## What the map shows

The Neighborhood map draws each followed road and where its exhaust goes now, from the same rule as the actions:

- **Likely exhaust:** the road reaches the home now. The plume is strongest at the road, fades to the rule's reach (500 m by day, 2 km in calm air before sunrise), then thins out.
- **Likely exhaust, carried away:** the wind takes the exhaust somewhere else. The same shape, paler and grey.
- Above the map, one sentence says whether a road's exhaust is likely to reach you and until when. Under the key, one line per road says where its exhaust goes and what changes it (the wind turning, the end of the busy hours, sunrise).

The map shows when exhaust is likely to reach the home, not how much: Home Air doesn't measure it. A PM2.5 monitor barely shows road exhaust: near a road, what rises is mostly ultrafine particles and NO₂, which PM2.5 mass hardly counts (Karner et al. 2010, above). The exhaust's colour follows the live sky's.

## Coverage

| Level | Place | Data |
|---|---|---|
| Measured | BBBike's NYC box, including parts of nearby New Jersey and lower Westchester | Roads, HPMS counts where matched, measured weekday slow hours where available; defaults for the road type elsewhere |
| Counted | The 50 largest US Census metropolitan statistical areas, ranked by July 2025 population | Roads and HPMS 2024 counts where matched; US default hours |
| Counted | The rest of the 50 states, including Alaska and Hawaii; DC is included | Regional road files, HPMS 2024 counts where matched; US default hours |

Traffic counts: BTS's HPMS rows for Texas, Massachusetts, Maryland and Tennessee carry almost no route names or numbers, so roads there have no counts (they keep the US default busy hours).

The [Census metro list](../scripts/roads/us-metros.json) records the sources, population ranking and core boxes for the 50 Counted metros. Each Counted metro's core is the bounding box of the whole Census area, including suburbs. Measured NYC uses the core in [metros.toml](../scripts/roads/metros.toml). Each road file includes a data margin beyond its core. Selection uses the core only: west and south edges are included; east and north edges are excluded, whether or not another core borders them. Longitude is normalized to `[-180°, 180°)`, so +180° and -180° select and search the same place. Measured NYC takes priority over a Counted metro. Between overlapping Counted metros, the smaller core wins, then its id. Outside the metros, the smaller regional core wins, then its id.

Regional cores start at 2° × 2° and split into smaller cores where needed to keep downloads small. In the 25 November 2026 build, half the 289 regional files are under about 60 KB; the largest is about 1.6 MB, compressed. Home Air uses the matching cached regional file when available; otherwise, it downloads the file only if the index supplies a matching entry and sha256. Each block has its own file and sha256 in `index.json`; there is no national archive to download.

Normal operation makes no road-data request. At start, Home Air retries a failed setup download only with a config file, outside hosted environments and containers, and when a `pending` marker makes a retry due (at most once a day). Separately, on this computer, a server or Railway, Home Air downloads missing files named by saved automatic roads, requesting `index.json` too if a trusted digest is missing. A restart on Railway can need these downloads because the cache is not retained. Home Air retains trusted digests by dated filename in `road-data/digests.json`. A missing automatic-road file is fetched under its saved name, provided that file is still published and a retained digest or index entry supplies its sha256. Otherwise, the roads are named but not drawn; Home Air never substitutes a newer file. Running setup again at the same place with automatic roads already saved keeps those roads and makes no road-data request; Home Air can still make its other address, timezone and monitor requests during setup.

The index's `coverage.US` lists every half-open 2° parent cell `[west, south, east, north]` that intersects the frozen footprint of the 50 states and DC. Parents remain covered when their blocks split or have no published road file. Coverage comes from the frozen footprint, independently of road files and their margins. These are grid cells, not the exact national boundary: coastal and border cells can include water or foreign land. Puerto Rico and other US territories are not part of the frozen footprint.

When no file covers a point, `./homeair setup` saves `none` inside this grid coverage and `not_built` outside it. The same west/south included and east/north excluded rules apply. With no configured roads or pending download, the page shows "Traffic · no main road within 1 km" for `none`. It shows "Traffic · road data isn't built for your area yet" only when it can read the saved `not_built` setup outcome in `road-data/outcome`. If that record is missing or unreadable, the page shows "Traffic · no main road within 1 km". A failed download has its own message.

## How to add a place or improve it

For contributors and coding agents:

1. For a separate metro, add an entry to `scripts/roads/metros.toml` and review the road names.
2. Install osmium-tool and the Python packages in `requirements.txt` and `requirements-dev.txt`, then build:

   ```bash
   python3 scripts/roads/build.py <metro> <input.osm.pbf> --out DIR
   ```

3. For US metros, add `--hpms-file /path/to/HPMS2024.zip` (or `--no-counts` to omit counts). Run `python3 scripts/roads/build.py --help` for optional flags, including `--osm-date YYYY-MM-DD` when the extract has no date header and `--previous DIR` for a stale speed feed. Add public test points beside a highway, between roads, in an interstate suburb and in a rural area. Outside the US, include local tagging and names.
4. Open a pull request with the checks and credits. A place is supported when its test points pass; reviewed name overrides are optional for the US build.

## Build and check US data

Install osmium-tool and the Python packages in `requirements.txt` and `requirements-dev.txt`. Supply the Geofabrik US extract. Download BTS’s `HPMS2024.zip` (2.6 GB) once from the [HPMS download page](https://geodata.bts.gov/datasets/5e6a977c2d7c4ec1bdc82e684d3384f2/about), then pass its path with `--hpms-file`. From the repository root:

```bash
python3 scripts/roads/build.py us us-latest.osm.pbf --out roads-out --work roads-work \
  --hpms-file /path/to/HPMS2024.zip
```

The build checks the HPMS zip’s byte size and sha256 against the pin in `scripts/roads/hpms.py` before reading counts or reusing count caches. HPMS is US public domain; credit: “FHWA, distributed by BTS (NTAD)”.

The build uses the frozen [Census metros](../scripts/roads/us-metros.json) and [state boundaries](../scripts/roads/us-states.json), reads HPMS 2024 counts by state from the local zip, and writes the 50 metro files, regional files, `index.json` and reports. Preserve the Measured NYC file and its index entry in the output folder when building a combined release. No NYC speed profiles are added to Counted files. A block with no main road in its core has no file; its 2° parent stays in `coverage.US`.

The [40 public test points](../tests/fixtures/roads/us-points.json) cover a highway, a home between roads, an interstate suburb and a rural home in each of ten metros. Expectations are checked against maps. To freeze their geometry from an extract:

```bash
python3 scripts/roads/freeze_us_clip.py us-latest.osm.pbf \
  --points tests/fixtures/roads/us-points.json --out tests/fixtures/roads/us-clip.json.gz
```

The build runs [check_us.py](../scripts/roads/check_us.py) before installing the index. To rerun the fixed points and rewrite the reports for all 50 metros in an existing output folder:

```bash
python3 - <<'PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, "scripts/roads")
from check_us import check_build
folder = Path("roads-out")
check_build(json.loads((folder / "index.json").read_text()), folder,
            Path("tests/fixtures/roads/us-points.json"))
PY
```

Check `roads-out/reports/us-build.json` for each state's count joins and errors, download sizes, disk use and build time. A failed count source must be reported; "Counted" does not mean every road has a matched count. The area samples in the metro reports include water and parks; their shares are not population estimates.
