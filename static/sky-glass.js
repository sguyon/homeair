// Everyday frosted glass. No data rules, autoplay, network calls or work until create(api).
var SkyGlass = (() => {
  const TARGET_CONTRAST = 5.4; // ratio, margin above AA for rounded browser compositing.
  const MIN_CONTRAST = 4.5; // ratio, required for composited ink, including opacity and inner films.
  const MIN_ALPHA = .5; // opacity, the picked level-1 frost always has a visible body.
  const ALPHA_MARGIN = 2 / 255; // opacity, two alpha levels protect against browser rounding after films are measured explicitly.
  const SEARCH_STEPS = 12; // iterations, sub-pixel opacity precision without continuous layout work.
  const SKY_ENDPOINTS = [[0, 0, 0], [255, 255, 255]]; // sRGB channels, 0–255; bound clouds, sun and lightning through frost.
  const INK_ENDPOINTS = [[0, 0, 0], [255, 255, 255]]; // sRGB channels, 0–255; endpoints for a verified readable ink of the same hue.
  const SURFACES = '.sum-card, .card:not(.off, .mc-ghost, .hist-off, .no-token-card), .bar .custom-top, .now-line .dsp-top';
  const TRANSIENT = '.sx-tip, .sx-hv, [data-chart-scrub]';
  const SCRUB_READOUT = '[data-chart-scrub="readout"]';
  const MATERIAL_PROPERTIES = ['--glass-tint', '--glass-alpha', '--glass-surface', '--glass-backdrop', '--glass-edge', '--glass-sheen'];
  // The cards' rim and top highlight in the pass: they take this share of the air's glass colour, at the theme's own opacities
  // [light, dark] (app.css --glass-edge, --glass-sheen), so no white line rings tinted glass (Sacha, 26 Nov: "the border/bevel
  // around the cards should also have the tint").
  const EDGE_AIR_SHARE = .85, EDGE_ALPHA = [.55, .10], SHEEN_ALPHA = [.55, .07];
  const VEIL_DARK_MAX = 225; // sRGB channel: a dark theme's air tint is scaled so its brightest channel is this, for the drawings' veil.
  const FILM_ROLES = ['row', 'open', 'highlight', 'meter', 'past']; // CSS token suffixes; bound inner films drawn behind card text, including SVG past-area fill.
  // Outdoor air (smoke, exhaust, pollen): all animation tuning is in CSS px, milliseconds or unitless shares.
  // The pass follows the picked prototype's timeline (mock 109 round 3, glass3.js): over the words for about a second, then a slow fade.
  const FRONT_IN_MS = 220; // ms, front matter arrives fast.
  // ms [held over the words until, then fades for]: smoke covers about 0.9 s and exhaust 0.5 s, then the air passes over about a second (the prototype's recordings).
  // Fine particles hold between the two (0.8 s), then fade like them.
  const FRONT_TIMING = { smoke: [850, 1550], exhaust: [500, 1400], particles: [800, 1450], pollen: [640, 810] };
  // exponent: the front fades as (1 - u)^this. The veil's layers stack, so the page brightens most as it nears 0; a slow tail
  // keeps every frame's change small (at most about 6 grey levels per 0.1 s), where a linear end popped at 1440.
  const FRONT_FADE_POWER = 2;
  const SKIP_FADE_MS = 250; // ms, a controller stop fades directly to the settled state.
  const AIR_IN_MS = 450; // ms, the air's colour comes in everywhere at once, fast.
  const PEAK_MS = 2500, SETTLE_MS = 3900; // ms, full strength while the front passes, then the slow fade to the held cast by 3.9 s.
  const MOTION_MS = 4200, PARTICLE_FADE_AT_MS = 3200; // ms, the pass's matter keeps moving through the settle and fades over its last second.
  const MAX_PASS_STEP_MS = 50; // ms, a stalled frame (a phone's first load) advances the pass by at most this, so it is seen rather than skipped.
  const HOLD_STRENGTH = .45; // opacity share, a quiet air cast stays in the sky until the condition clears.
  // opacity share per kind: the sky's settled cast, nearer the prototype's amber smoke and grey-brown exhaust. The cards take the air's
  // colour only during the pass and return to the everyday glass with its fade (Sacha, 25 Nov): settled, the colour is only in the sky.
  const AIR_HOLD = { smoke: .65, exhaust: .7, particles: .6, pollen: .55 };
  const holdOf = kind => AIR_HOLD[kind] ?? HOLD_STRENGTH;
  const AIR_GLASS_ALPHA = { smoke: [.8, .86], exhaust: [.62, .74], particles: [.78, .86], pollen: [.5, .86] }; // opacity [light, dark] at full strength: every card takes the air's colour; pollen's light glass stays clearer so white dust shows, and exhaust's so the plume shows through.
  const SOFT_BUFFER_SCALE = .25; // buffer/CSS px ratio, soft viewport fields need only quarter resolution.
  const FRONT_SOFT_SCALE = .5; // buffer/CSS px ratio, the veil over the words at half resolution: a quarter-size buffer showed a faint 4 px grid when stretched.
  const CRISP_DPR_MAX = 1.5, CRISP_PIXEL_MAX = 2000000; // px density/count, cap the two crisp layers on large/high-DPR screens.
  // The exhaust (mock 109 round 4, picked 26 Nov): a plume from the road's compass side (north up, as on the map) drifting
  // with the wind; soot specks that cross in front of the cards in the opening, then drift behind the glass; strength by
  // the pollution; colour by the weather and the hour, shared with the map's plume through --air-exhaust-map.
  // sRGB [day, night] per role: LA's brown lid (round 2's E), 80s LA dusk (F), and grey for cloud and rain.
  const EXHAUST_PAL = {
    la: { edge: [[214, 194, 160], [168, 112, 62]], body: [[164, 144, 116], [98, 70, 46]], foot: [[122, 104, 84], [58, 42, 30]], soot: [[44, 36, 30], [214, 178, 136]] },
    dusk: { edge: [[250, 186, 150], [196, 110, 92]], body: [[166, 128, 116], [100, 62, 64]], foot: [[120, 92, 88], [58, 38, 44]], soot: [[52, 34, 38], [226, 170, 160]] },
    grey: { edge: [[150, 141, 130], [104, 97, 88]], body: [[134, 126, 116], [78, 72, 65]], foot: [[98, 91, 84], [44, 40, 36]], soot: [[34, 30, 28], [150, 144, 134]] },
  };
  const EXHAUST_DUSK_EL = [4, 9], EXHAUST_DUSK_MAX = .55; // degrees, share: the dusk colours peak with the sun 4° up, gone 9° either side, at most this share of them ("slightly more 80s").
  const EXHAUST_CLOUD = [35, 55], EXHAUST_RAIN_GREY = .6, EXHAUST_THUNDER_GREY = .4; // percent: cloud from which it greys, fully grey 55 points later; shares of grey in full rain and in a storm.
  // Strength: shoulder traffic's share, the weakest it gets, its share at the PM2.5 limit (full at the "high" line), the wind
  // (km/h) from which it thins and over how many km/h, by how much, and rain's thinning.
  const EXHAUST_STRENGTH = { high: .7, floor: .4, pmBase: .55, windFrom: 12, windSpan: 25, windThin: .3, rainThin: .45 };
  const EXHAUST_CALM_KMH = 3, EXHAUST_BEARING = 135; // km/h under which the wind's direction is noise (the plume drifts straight away from the road); degrees, the road's side when nothing says (the reference home's BQE).
  const EXHAUST_SPEED = [8, 2]; // CSS px/s with no wind, plus this per km/h: a light wind creeps.
  const EXHAUST_FRONT_ALPHA = .5; // share of the plume over the words in the opening: soft enough to read through.
  const EXHAUST_SKY_HOLD = .45; // opacity share of the plume behind the glass once settled.
  const EXHAUST_MAP_EDGE = .3; // share of the sunlit edge in the map plume's colour (the body, a little lighter).
  // The plume's colour over the whole sky, behind the glass: this opacity share at the road's side and at the far side, so the
  // sky around the cards takes the exhaust too (Sacha, 26 Nov: "the effect/colors only shows in the cards not the background").
  const PLUME_WASH = [.62, .28];
  const PLUME_PUFFS = 16, PLUME_GLOW = [1, .65, .9]; // billows riding the wind; the glow's opacity at the road's edge and midway, and its reach (share of the diagonal).
  const SOOT_COUNT = [1, 200, 700], SOOT_NEAR = .18; // specks per CSS px of width, min, max; share near (soft and larger).
  const SOOT_FRONT = [250, 2600, 1400], SOOT_WORD_DIM = .5; // ms: in front of the cards they fade in, stay until 2.6 s, then fade over 1.4 s; half strength over words.
  const SOOT_ARRIVE_MS = 1500, SOOT_GUST = [.55, 2200]; // ms: they pour in from the road's side; a gust carries them this share of the diagonal, slowing to the wind's pace by 2.2 s.
  const SOOT_SETTLED = { every: 3, alpha: .55, slow: .5 }; // settled behind the glass: every third speck, this bright, at this share of the speed.
  const SOOT_RAIN_FALL = 90; // CSS px/s: rain pulls the specks down.
  const SIDE_BEARING = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 }; // degrees per compass side.
  const mix3 = (a, b, share) => a.map((v, i) => v + (b[i] - v) * share);
  const unit = v => Math.max(0, Math.min(1, v));
  const smoothstep = v => (v = unit(v)) * v * (3 - 2 * v);
  // The exhaust's colours under this sky: per role, [day, night].
  function exhaustPalette({ el = 30, cloud = 0, rain = 0, thunder = false } = {}) {
    const dusk = EXHAUST_DUSK_MAX * smoothstep(1 - Math.abs(el - EXHAUST_DUSK_EL[0]) / EXHAUST_DUSK_EL[1]);
    const grey = unit(unit((cloud - EXHAUST_CLOUD[0]) / EXHAUST_CLOUD[1]) + EXHAUST_RAIN_GREY * unit(rain) + (thunder ? EXHAUST_THUNDER_GREY : 0));
    const pal = {};
    for (const role of Object.keys(EXHAUST_PAL.la)) pal[role] = [0, 1].map(i => mix3(mix3(EXHAUST_PAL.la[role][i], EXHAUST_PAL.dusk[role][i], dusk), EXHAUST_PAL.grey[role][i], grey));
    return { pal, dusk, grey };
  }
  // The bearing from home to the road: its compass side, else the middle of the arc the wind must blow from, else the wind.
  function roadBearing(road, toward, windDir) {
    if (road && Object.hasOwn(SIDE_BEARING, road.side)) return SIDE_BEARING[road.side];
    if (Array.isArray(toward) && toward.length === 2) return toward[0] <= toward[1] ? (toward[0] + toward[1]) / 2 : ((toward[0] + toward[1] + 360) / 2) % 360;
    return Number.isFinite(windDir) ? windDir : EXHAUST_BEARING;
  }
  // Where the plume enters a W × H screen (north up: the road's side, on the edge; southeast is the bottom-right corner)
  // and the unit direction it drifts (the way the wind blows; in a calm, straight away from the road).
  function plumeFrame({ bearing, windDir, windSpeed = 0, W, H }) {
    const rad = d => d * Math.PI / 180, ex = Math.sin(rad(bearing)), ey = -Math.cos(rad(bearing)), n = 1 / Math.max(Math.abs(ex), Math.abs(ey));
    const from = windSpeed < EXHAUST_CALM_KMH || !Number.isFinite(windDir) ? bearing : windDir;
    return { origin: { x: W / 2 + ex * n * W / 2, y: H / 2 + ey * n * H / 2 }, dx: -Math.sin(rad(from)), dy: Math.cos(rad(from)),
      L: Math.hypot(W, H), speed: EXHAUST_SPEED[0] + EXHAUST_SPEED[1] * windSpeed };
  }
  // 0.4–1: peak or shoulder traffic, outdoor PM2.5 against the home's limits, thinner in a strong wind or rain.
  function exhaustStrength({ strength, pm, limits, windSpeed = 0, rain = 0 } = {}) {
    const lim = limits && limits.pm || 15, high = limits && limits.pm_high || 35, S = EXHAUST_STRENGTH;
    const level = Number.isFinite(pm) ? unit((pm - lim) / (high - lim)) : 0;
    return Math.max(S.floor, (strength === 'high' ? S.high : 1) * (S.pmBase + (1 - S.pmBase) * level)
      * (1 - S.windThin * unit((windSpeed - S.windFrom) / S.windSpan)) * (1 - S.rainThin * unit(rain)));
  }
  const AIR_KINDS = ['smoke', 'exhaust', 'particles', 'pollen']; // Outdoor condition kinds; the controller passes only the one shown (one effect per layer).
  const AIR_ROLES = { smoke: ['top', 'horizon', 'volume', 'front', 'foot', 'sun', 'glow', 'ash0', 'ash1', 'ash2', 'rim', 'tint'],
    exhaust: ['tint'],
    particles: ['top', 'horizon', 'volume', 'front', 'foot', 'soot', 'tint'],
    pollen: ['wash', 'dust', 'shade', 'fluff', 'glint', 'glare', 'beam', 'tint'] }; // CSS token suffixes, shared with app.css.
  const POLLEN_WASH_ALPHA = .16; // opacity, a quiet neutral veil rather than another smoke volume.
  // Pollen is the prototype's white dust and cottonwood: many fine motes and a few tufts drifting sideways, never falling like snow.
  const POLLEN_MOTE_AREA = 1500, POLLEN_MOTES = [150, 520]; // CSS px² of screen per mote, and the count's range.
  const POLLEN_TUFT_AREA = 11000, POLLEN_TUFTS = [26, 70]; // CSS px² of screen per tuft, and the count's range.
  const POLLEN_MOTE_PX = [.5, 1.05], POLLEN_NEAR_MOTE_PX = [1.5, .8]; // CSS px, a mote's radius (base, depth share) and a near mote's (base, random share).
  const POLLEN_TUFT_PX = [7, 11, 3], POLLEN_NEAR_TUFT_PX = [18, 10]; // CSS px, a tuft's radius (base, depth share, random) and a near tuft's (base, random).
  const POLLEN_MOTE_SPEED = [26, 84], POLLEN_TUFT_SPEED = [30, 90]; // CSS px/s, sideways speed (base, depth share) before the wind's pace.
  const POLLEN_PACE = [.75, .7], POLLEN_PACE_WIND_KMH = 25; // share, the wind's pace (base, extra at this wind speed and above).
  const POLLEN_NEAR_Z = [.94, .86], POLLEN_FRONT_Z = [.5, .45], POLLEN_FRONT_SHARE = [.5, .55]; // depth (mote, tuft): near ones, and from what depth and how many cross in front.
  const POLLEN_WOBBLE_PX = [5, 14], POLLEN_BOB_PX = [8, 14]; // CSS px, motes' and tufts' slow rise and sink (base, depth share).
  const POLLEN_SETTLE_EVERY = 3, POLLEN_SETTLE_TUFTS = [10, 24], POLLEN_SETTLE_SLOW = .5; // count/share, settled: every third mote, 10 (phone) or 24 tufts, at half speed.
  const POLLEN_GLASS_MIN_Z = .3, POLLEN_GLASS_TUFT = .75, POLLEN_GLASS_THEME = [1, .32]; // depth/opacity shares [light, dark]: the pass's other grains drawn as the prototype drew them in the glass; white on dark glass reads at once, so less there.
  const POLLEN_SETTLE_THEME = [1, .35]; // opacity share [light, dark]: the settled drift stays light and sporadic (white on dark moved 3.7x the prototype).
  const WORD_DIM = .25, WORDS_TTL_MS = 700, WORDS_ROW_PX = 32; // opacity share/ms/CSS px: a grain over a word fades to a quarter (white over ink would drop under 4.5:1); text boxes are re-read this often, bucketed in rows.
  const POLLEN_SETTLE_FPS = 30, POLLEN_SETTLE_IN_MS = 1600, POLLEN_SETTLE_GROW = 1.3; // frames/s, ms and size share: the settled drift fades in and reads at a glance.
  const FLUFF_SPRITES = 6, FLUFF_SPRITE_PX = 96, FLUFF_SOFT_PX = 36, FLUFF_RADIUS = .4; // count/buffer px/share: cottonwood sprites, a blurred copy, and the tuft's visible radius in its sprite.
  const FLUFF_FIBRES = 40, FLUFF_STRAYS = 4, FLUFF_CLUMPS = 7; // count, fibres fanning from a seed, short strays and cottony clumps per sprite.
  const TINTED_SPRITES_MAX = 64; // count, tinted sprite copies kept before the cache starts again (night light shifts the colours slowly).
  const POLLEN_SHADE_RGB = [112, 104, 90], POLLEN_SEED_RGB = [128, 104, 74], POLLEN_MOTE_EDGE_RGB = [112, 108, 100]; // sRGB, a warm-grey shade under a tuft and a mote on light skies, the darker seed.
  const POLLEN_BEAM_WIDTH = .26, POLLEN_BEAM_MIN_PX = 600; // share of width (at least this many CSS px), the soft sunbeam the dust shows in.
  const POLLEN_BEAM_ALPHA = [.5, .3], POLLEN_GLARE_ALPHA = [.3, .4], POLLEN_NIGHT_GLARE = .12; // opacity [light, dark]: cream sunbeam and sun glare; their share left at night.
  const POLLEN_GLINT_AT_MS = 700, POLLEN_GLINT_IN_MS = 380, POLLEN_GLINT_OUT_MS = 520, POLLEN_GLINT_END_MS = 1500, POLLEN_GLINT_PX = 7, POLLEN_GLINT_GROW_PX = 4; // ms/CSS px, one mote catches the sun once.
  const POLLEN_GLARE_REACH = .6, POLLEN_NIGHT_WASH = .6, POLLEN_NIGHT_BEAM = .7; // share of the longer side/shares taken off at night: the glare's radius, the haze and the beam fade in the dark.
  const POLLEN_BEAM_STOPS = [[0, 0], [.1875, .135], [.344, .61], [.5, 1], [.656, .61], [.8125, .135], [1, 0]]; // across-beam share/opacity share, a sampled Gaussian.
  const POLLEN_MOTE_SHADOW_ALPHA = .55, POLLEN_MOTE_SHADOW_PX = [.2, .7]; // opacity share/CSS px offset, a mote's shadow on light skies.
  const POLLEN_TUFT_SHADE_ALPHA = [.7, .5], POLLEN_TUFT_SHADE_OFFSET = [.05, .14]; // opacity share (crisp, soft)/radius shares: a soft warm-grey shade just below, off-white on pale glass reads like cotton on paper.
  const POLLEN_WRAP_PX = [30, 40], POLLEN_TUFT_SWAY_PX = 8; // CSS px: motes and tufts wrap this far off screen; a tuft's sideways sway.
  const POLLEN_TUFT_SPIN = .18, POLLEN_TUFT_SQUASH = [.8, .2]; // radians per s/scale (base, swing), a sparse tuft turns and tumbles a little as it drifts.
  // Fine particles: no direction and no source. Smoke's wash and broad volumes in a neutral grey-beige, even over
  // the whole sky (no lid, no wall rising from a side), and the exhaust's fine dark soot specks wandering slowly instead of riding the wind.
  const FINE_WASH_ALPHA = .62, FINE_FRONT_ALPHA = .78; // opacity at full strength: the even haze behind the glass, and over the words during the hold.
  const FINE_BILLOW_ALPHA = .32, FINE_FRONT_BILLOW_ALPHA = .42; // opacity: smoke's volumes, faint, so the haze has body without reading as a plume.
  const FINE_BILLOW_DRIFT = .35, FINE_SPECK_DRIFT = .3; // share of smoke's and soot's sideways speed: the haze hangs, the specks wander.
  const FINE_SPECK_SIZE = .8, FINE_SPECK_ALPHA = .85; // share of soot's size/opacity: fine dark specks, every one the same kind of grain.
  const FINE_SETTLE_SPECK_EVERY = 3, FINE_SETTLE_SPECK_ALPHA = [.5, .4], FINE_SPECK_SLOW = .5; // every third speck stays behind the glass once settled; opacity [light, dark]; time share.
  const SPARSE_PARTICLE_MAX = 8; // count, at most eight per settled burst, which stays behind the glass.
  const SPARSE_DURATION_MS = 3600, SPARSE_STAGGER_MS = 600, SPARSE_FADE_MS = 700; // ms, a finite drift with soft arrival/exit, then no RAF work.
  const SPARSE_BACK_ALPHA = .16; // opacity, behind the glass.
  const NIGHT_LUMINANCE = .2, NIGHT_RANGE = .16; // relative luminance, smoothly darken atmospheric matter at night/in storms.
  const PHONE_WIDTH = 700; // CSS px, under this the cards fill a phone's width.
  const SMOKE_TOP_ALPHA = .88, SMOKE_WASH_ALPHA = .8; // opacity shares, the whole sky turns amber-grey, heavier toward the horizon (the prototype's wash).
  const SMOKE_WALL_ROLL_MS = 550, SMOKE_WALL_FROM = 1.05, SMOKE_WALL_TRAVEL = 1.35; // ms/height shares, the front wall rolls up from below the horizon past the top.
  const SMOKE_WALL_SOFT = .18, SMOKE_WALL_RAMP = .14, SMOKE_WALL_ALPHA = .72, SMOKE_WALL_FOOT_ALPHA = .88; // height shares/opacity, its soft top edge and its body, heavier low.
  const SMOKE_WALL_PUFFS = [20, 30], SMOKE_WALL_R = [.3, .32], SMOKE_WALL_RISE = [.5, .75], SMOKE_WALL_RISE_MS = 620; // count (phone, wide)/min-side shares/height shares/ms, billows rolling up through the wall.
  const SMOKE_WALL_PUFF_ALPHA = [.55, .4], SMOKE_WALL_LIFT_PX = 30, SMOKE_WALL_SWAY_PX = 12; // opacity (base, random)/CSS px per s/CSS px, so it reads as smoke, not a colour.
  const SMOKE_WALL_ASPECT = [1.7, 1.05], SMOKE_WALL_SWAY_HZ = 1.3, SMOKE_WALL_PALE_ALPHA = .55, SMOKE_WALL_DARK_ALPHA = .5; // radius multiples/radians per s/opacity shares: broad billows, every third one pale.
  const SMOKE_PALE_RGB = [236, 226, 214], SMOKE_PALE_SHARE = [.45, .2]; // sRGB and share [day, night], the pale billows lit inside the wall.
  const BILLOW_COUNT = 16, BILLOW_MIN = .08, BILLOW_RANGE = .13, BILLOW_ASPECT = 1.55; // count/viewport shares, broad smoke volumes rather than ribbons.
  const BILLOW_ALPHA = .65, BILLOW_DRIFT = .025, BILLOW_BOB_MS = 2400, BILLOW_BOB_PX = 14; // opacity/px per ms/ms/px, slow wind-borne smoke.
  const BILLOW_RISE_MS = 850, BILLOW_START = .86; // ms and height share, billows roll up from the horizon to fill the sky.
  const PARTICLE_COUNT = 100, FRONT_PARTICLE_EVERY = 3, PARTICLE_ALPHA = .8; // count/share/opacity, crisp ash/soot during the short pass only.
  const ASH_MIN_PX = 1.1, ASH_RANGE_PX = 3.8, ASH_VERTICES = 7, ASH_ASPECT = .7; // CSS px/count/share, irregular curled flakes never become hair-like strands.
  const ASH_FEATURE_EVERY = 13, ASH_FEATURE_SCALE = 1.8; // particle count/radius share, a few larger curled flakes among fine ash.
  const ASH_SPIN = .7, ASH_RIM_MIN_PX = .7, ASH_RIM_SHARE = .22, SOOT_SIZE = .52; // radians per s/CSS px/shares, curled grey ash differs from fine soot.
  const DRIFT_MIN_PX = 16, DRIFT_RANGE_PX = 30, WIND_SPEED_SCALE = 1.5; // CSS px/s and px/s per km/h, use the existing wind for travel.
  const PARTICLE_MARGIN = 30, PARTICLE_BOB_PX = 4; // CSS px, wrap offscreen and tumble without a falling-rain pattern.
  const SUN_SIZE = .022, SUN_MIN_PX = 9, SUN_MAX_PX = 18, SUN_REACH = 6; // viewport share/CSS px/radius multiple, small soft sun wholly in open sky.
  const SUN_MIN_ELEVATION = 6, SUN_CLOUD_MAX = 30; // degrees/percent, no smoke sun at night, under clouds, or in rain/storms.
  const SUN_GLOW_ALPHA = .45, SUN_DISC_ALPHA = .8, SUN_DISC_SCALE = 1.6; // opacities/radius share, dull diffuse light instead of a flat red disc.
  const SUN_AZ_CENTER = 180, SUN_AZ_SPAN = 240; // degrees, project east/west azimuth into the open viewport.
  const SUN_X_CENTER = .5, SUN_X_MARGIN = .04, SUN_Y_BASE = .7, SUN_Y_PER_DEG = .014; // viewport shares/share per degree, match the native sun projection.
  const SUN_Y_MIN = .06, SUN_Y_MAX = .78; // viewport shares, keep the smoke sun inside the sky.
  const PUFF_STOPS = [[0, 1], [.42, .8], [.72, .28], [1, 0]]; // radius/opacity shares, readable billow cores with a soft fringe.
  const ASH_POLY_MIN = .55, ASH_POLY_RANGE = .45; // radius shares, irregular flake silhouette.
  const ASH_FLAT = .66, ASH_TUMBLE = .34; // height shares, a visible curled face as the flake turns.
  const ASH_RIM_BLEND = .5, ASH_RIM_ALPHA = .8; // color/opacity shares, a light curled edge on grey ash.
  const ASH_CURL_Y = -.1, ASH_CURL_RX = .8, ASH_CURL_RY = .3; // flake-radius shares, broad curl rather than a thin strand.
  const ASH_CURL_START = 1.08, ASH_CURL_END = 1.92; // half-turns, open arc showing the rolled edge.
  const clamp = (v, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));
  const over = (bg, rgb, alpha) => bg.slice(0, 3).map((v, i) => v + (rgb[i] - v) * alpha);
  const linear = v => (v /= 255) <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
  const luminance = rgb => .2126 * linear(rgb[0]) + .7152 * linear(rgb[1]) + .0722 * linear(rgb[2]);
  const contrast = (a, b) => (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
  const parse = value => {
    value = value.trim();
    if (/^#[0-9a-f]{3}$/i.test(value)) value = '#' + [...value.slice(1)].map(c => c + c).join('');
    if (/^#[0-9a-f]{6}$/i.test(value)) return [1, 3, 5].map(i => parseInt(value.slice(i, i + 2), 16));
    const parts = value.match(/-?[\d.]+/g);
    if (!parts || parts.length < 3) return null;
    const rgb = parts.map(Number);
    return value.startsWith('color(srgb ') ? rgb.map((v, i) => i < 3 ? v * 255 : v) : rgb;
  };
  const frost = (rgb, brightness, saturation) => {
    const grey = rgb[0] * .213 + rgb[1] * .715 + rgb[2] * .072;
    return rgb.map(v => clamp((grey + (v - grey) * saturation) * brightness, 0, 255));
  };

  // Composite every inner film and opacity group twice: once with the glyph and once without.
  // Testing raw ink against the card alone misses faint labels and tinted/striped table rows.
  function textContrast(entry, bg) {
    const parents = [];
    let surface = bg;
    for (const layer of entry.path) {
      parents.push(surface);
      if (layer.rgb) surface = over(surface, layer.rgb, layer.rgb[3] ?? 1);
    }
    let glyph = over(surface, entry.rgb, entry.alpha), empty = surface;
    for (let i = entry.path.length - 1; i >= 0; i--) {
      glyph = over(parents[i], glyph, entry.path[i].opacity);
      empty = over(parents[i], empty, entry.path[i].opacity);
    }
    return contrast(luminance(glyph), luminance(empty));
  }

  // Outdoor air owns drawing only: the controller elects a leader and decides when to play.
  function createOutdoor(api, onDraw) {
    const html = document.documentElement, layers = [], samples = [];
    let W = 1, H = 1, dpr = 1, frame = 0, destroyed = false, paused = document.hidden, geometryDirty = true, resizeDue = false;
    let conditions = [], primary = null, run = null, playing = null, age = MOTION_MS, strength = HOLD_STRENGTH, front = 0, cast = 0;
    let palette = {}, bounds = [], sun = null, sunPoint = null, beam = null, night = 0, travel = 1, pace = 1;
    let sparse = null, motionStill = false, sunCover = 0, drift = null, frameDue = false, shownStrength = HOLD_STRENGTH;
    let plume = null, exhaustPal = null, puffSprite = null; const soot = [];   // the exhaust's frame and strength, its colours, the soft dot, the specks
    const particles = [], billows = [], wall = [], motes = [], tufts = [];
    const rgba = (rgb, a) => `rgb(${rgb.map(Math.round).join(' ')} / ${clamp(a)})`;
    const reduced = () => typeof api.reduce === 'function' ? api.reduce() : !!api.reduce;
    const dark = () => typeof api.dark === 'function' && api.dark();
    const smooth = v => (v = clamp(v)) * v * (3 - 2 * v);
    const ease = v => 1 - (1 - clamp(v)) ** 3;
    const wrap = (v, span) => ((v % span) + span) % span;
    const wrapIn = (v, lo, hi) => lo + wrap(v - lo, hi - lo);
    // The controller holds the air back while the load pass may still come, so the clear page shows first.
    const veiled = () => !run && typeof api.loadPending === 'function' && api.loadPending();
    // The prototype's envelopes: the front eases in, holds, then fades; the colour eases in, holds, then settles to the cast.
    const frontEnd = kind => FRONT_TIMING[kind][0] + FRONT_TIMING[kind][1];
    const passFront = (t, kind) => {
      const [hold, fade] = FRONT_TIMING[kind];
      if (t < FRONT_IN_MS) return ease(t / FRONT_IN_MS);
      if (t < hold) return 1;
      return (1 - clamp((t - hold) / fade)) ** FRONT_FADE_POWER;
    };
    // The cards' share of the air's colour: in with the sky, then back to the everyday glass over the sky's settle (PEAK_MS → SETTLE_MS).
    const passCast = (t, from) => t < AIR_IN_MS ? from + (1 - from) * ease(t / AIR_IN_MS) : 1 - smooth((t - PEAK_MS) / (SETTLE_MS - PEAK_MS));
    const passStrength = (t, from, kind) => t < AIR_IN_MS ? from + (1 - from) * ease(t / AIR_IN_MS)
      : holdOf(kind) + (1 - holdOf(kind)) * (1 - smooth((t - PEAK_MS) / (SETTLE_MS - PEAK_MS)));
    const rollQ = t => { const p = clamp(t / SMOKE_WALL_ROLL_MS); return smooth(p) * .6 + p * .4; };
    function canvas(parent, name, scale) {
      const c = document.createElement('canvas');
      c.className = 'sky-air-layer ' + name; c.setAttribute('aria-hidden', 'true');
      // Native rain, flash and bolt keep their drawing order above the outdoor fields.
      const before = parent === api.back ? parent.querySelector('#sky-rain') : parent.querySelector('#front-flash, #sky-bolt');
      parent.insertBefore(c, before); const ctx = c.getContext('2d');
      const layer = { c, ctx, scale }; layers.push(layer); return layer;
    }
    const backSoft = canvas(api.back, 'sky-air-back-soft', SOFT_BUFFER_SCALE);
    const backFine = canvas(api.back, 'sky-air-back-fine', 1);
    const frontSoft = canvas(api.front, 'sky-air-front-soft', FRONT_SOFT_SCALE);
    const frontFine = canvas(api.front, 'sky-air-front-fine', 1);
    // Pollen's settled drift has its own crisp layer behind the cards, seen through the frosted glass and in the open sky:
    // redrawn alone, never sampled for ink or re-solving glass, each grain dimmed where it crosses words on the sky (wordsDim).
    const backDrift = canvas(api.back, 'sky-air-back-drift', 1);
    // Text line boxes on the page, bucketed by rows, re-read at most every WORDS_TTL_MS and after a scroll.
    let words = null;
    function readWords(now) {
      if (words && now - words.at < WORDS_TTL_MS) return;
      const rows = new Map(), page = document.querySelector('.page') || document.body;
      words = { at: now, rows };
      if (typeof document.createTreeWalker !== 'function') return;
      const range = document.createRange(), walker = document.createTreeWalker(page, 4);   // NodeFilter.SHOW_TEXT
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const el = n.parentElement;
        if (!n.nodeValue.trim() || !el || el.closest('.now-bar, [aria-hidden="true"]')) continue;
        range.selectNodeContents(n);
        for (const r of range.getClientRects()) {
          if (!r.width || r.bottom < -WORDS_ROW_PX || r.top > H + WORDS_ROW_PX) continue;
          for (let row = Math.floor(r.top / WORDS_ROW_PX); row <= Math.floor(r.bottom / WORDS_ROW_PX); row++) {
            if (!rows.has(row)) rows.set(row, []); rows.get(row).push([r.left, r.top, r.right, r.bottom]);
          }
        }
      }
    }
    // A grain whose dense middle crosses a line of text fades to WORD_DIM: the words keep their contrast, nothing is cut around them.
    function wordsDim(x, y, r) {
      if (!words) return 1;
      let d = Infinity;
      for (let row = Math.floor((y - r) / WORDS_ROW_PX); row <= Math.floor((y + r) / WORDS_ROW_PX); row++) {
        for (const [l, t, rr, b] of words.rows.get(row) || []) d = Math.min(d, Math.hypot(Math.max(l - x, 0, x - rr), Math.max(t - y, 0, y - b)));
      }
      return WORD_DIM + (1 - WORD_DIM) * smooth(d / (r * .5 + 1));
    }
    const sprites = { fluff: [], soft: [], seed: [], tinted: new Map() };
    function fluffSprites() {
      if (sprites.fluff.length) return;
      for (let v = 0; v < FLUFF_SPRITES; v++) {
        let s = 101 + v * 17;
        const R = () => { s = Math.imul(s, 1664525) + 1013904223 | 0; return (s >>> 0) / 4294967296; };
        const S = FLUFF_SPRITE_PX, c = S / 2, sx = c - S * (.2 + R() * .06), sy = c + (R() - .5) * S * .08, tilt = (R() - .5) * .5;
        const o = document.createElement('canvas'); o.width = o.height = S; const g = o.getContext('2d');
        for (let i = 0; i < FLUFF_CLUMPS; i++) {   // the cottony clumps
          const d = S * (.1 + R() * .3), a = tilt + (R() - .5) * 1.2, x = sx + Math.cos(a) * d, y = sy + Math.sin(a) * d * .85, rr = S * (.045 + R() * .075);
          const rg = g.createRadialGradient(x, y, 0, x, y, rr); rg.addColorStop(0, `rgb(255 255 255 / ${.3 + R() * .2})`); rg.addColorStop(1, 'rgb(255 255 255 / 0)');
          g.fillStyle = rg; g.fillRect(0, 0, S, S);
        }
        g.lineCap = 'round';
        // Dense near the seed, wispy at the ends: one-sided fibres, never a radial starburst.
        const fibre = (a, l, bend, al, w) => {
          const x0 = sx + (R() - .5) * 2, y0 = sy + (R() - .5) * 2, mx = x0 + Math.cos(a + bend) * l * .5, my = y0 + Math.sin(a + bend) * l * .5;
          const x2 = x0 + Math.cos(a + bend * .2) * l, y2 = y0 + Math.sin(a + bend * .2) * l, hx = (x0 + mx) / 2 + (mx - x0) * .2, hy = (y0 + my) / 2 + (my - y0) * .2;
          g.lineWidth = w; g.strokeStyle = `rgb(255 255 255 / ${al})`; g.beginPath(); g.moveTo(x0, y0); g.quadraticCurveTo(hx, hy, mx, my); g.stroke();
          g.strokeStyle = `rgb(255 255 255 / ${al * .45})`; g.lineWidth = w * .75; g.beginPath(); g.moveTo(mx, my);
          g.quadraticCurveTo(mx + (x2 - mx) * .5 + (R() - .5) * 3, my + (y2 - my) * .5 + (R() - .5) * 3, x2, y2); g.stroke();
        };
        for (let i = 0; i < FLUFF_FIBRES; i++) fibre(tilt + (R() - .5) * 1.8, S * (.14 + R() ** .7 * .32), (R() - .5) * .9, .26 + R() * .36, 1 + R() * 1.2);
        for (let i = 0; i < FLUFF_STRAYS; i++) fibre(tilt + Math.PI * (.55 + R() * .9) * (R() < .5 ? -1 : 1), S * (.06 + R() * .1), (R() - .5) * .8, .18 + R() * .18, .9);
        // Out of focus: down and up again (canvas blur filters are not in every browser).
        const small = document.createElement('canvas'); small.width = small.height = FLUFF_SOFT_PX; small.getContext('2d').drawImage(o, 0, 0, FLUFF_SOFT_PX, FLUFF_SOFT_PX);
        const b = document.createElement('canvas'); b.width = b.height = S; b.getContext('2d').drawImage(small, 0, 0, S, S);
        o.dataset.sprite = 'f' + v; b.dataset.sprite = 's' + v;
        sprites.fluff.push(o); sprites.soft.push(b); sprites.seed.push([(sx - c) / S, (sy - c) / S]);
      }
    }
    function tinted(sprite, rgb) {
      const key = sprite.dataset.sprite + rgb.map(v => Math.round(v / 4) * 4).join(',');
      let o = sprites.tinted.get(key);
      if (o) return o;
      o = document.createElement('canvas'); o.width = sprite.width; o.height = sprite.height; const g = o.getContext('2d');
      g.drawImage(sprite, 0, 0); g.globalCompositeOperation = 'source-in'; g.fillStyle = rgba(rgb, 1); g.fillRect(0, 0, o.width, o.height);
      if (sprites.tinted.size >= TINTED_SPRITES_MAX) sprites.tinted.clear();
      sprites.tinted.set(key, o); return o;
    }
    // Ink reads these small composites, never a full-DPR readback or a stale legacy canvas.
    for (let i = 0; i < 2; i++) {
      const c = document.createElement('canvas'); samples.push({ c, ctx: c.getContext('2d', { willReadFrequently: true }), pixels: null });
    }
    function report(kind) { if (playing !== kind) { playing = kind; if (api.onPlaying) api.onPlaying(kind); } }
    function cancel() {
      if (!frame) return;
      if (api.cancelOutdoorFrame) api.cancelOutdoorFrame(frame);
      else cancelAnimationFrame(frame);
      frame = 0;
    }
    // rear: whether this frame changes the rear scene that wet glass refracts (the settled drift alone does not).
    function schedule(rear = true) {
      if (!destroyed && !paused && !frame) frame = api.requestOutdoorFrame ? api.requestOutdoorFrame(tick, rear) : requestAnimationFrame(tick);
    }
    function readPalette() {
      const root = getComputedStyle(html);
      for (const kind of AIR_KINDS) {
        palette[kind] = {};
        for (const role of AIR_ROLES[kind]) palette[kind][role] = parse(root.getPropertyValue(`--air-${kind}-${role}`));
      }
      night = clamp((NIGHT_LUMINANCE - luminance(api.sky().top)) / NIGHT_RANGE);
    }
    const colour = (kind, role) => {
      if (kind === 'exhaust' && exhaustPal && exhaustPal[role]) return over(exhaustPal[role][0], exhaustPal[role][1], night);
      const c = palette[kind][role];
      // Material follows the theme; open-sky matter follows actual night/storm light.
      return c.length > 3 ? over(c.slice(0, 3), c.slice(3, 6), night) : c;
    };
    function geometry() {
      if (!geometryDirty) return;
      geometryDirty = false;
      bounds = [...document.querySelectorAll(SURFACES + ', .now-ctrl')].map(el => el.getBoundingClientRect()).filter(r => r.width && r.height);
      const pos = api.sun(), r = clamp(Math.min(W, H) * SUN_SIZE, SUN_MIN_PX, SUN_MAX_PX);
      const x = clamp(SUN_X_CENTER + (pos.az - SUN_AZ_CENTER) / SUN_AZ_SPAN, SUN_X_MARGIN, 1 - SUN_X_MARGIN) * W, y = clamp(SUN_Y_BASE - pos.el * SUN_Y_PER_DEG, SUN_Y_MIN, SUN_Y_MAX) * H;
      const weather = api.sky();
      const open = pos.el > SUN_MIN_ELEVATION && weather.cloud < SUN_CLOUD_MAX && !weather.rain && !weather.thunder &&
        !bounds.some(b => x + r * SUN_REACH > b.left && x - r * SUN_REACH < b.right && y + r * SUN_REACH > b.top && y - r * SUN_REACH < b.bottom);
      sun = open ? { x, y, r } : null;
      const wind = api.wind(), road = conditions.find(c => c.kind === 'exhaust');
      travel = Math.sin((wind.dir || 0) * Math.PI / 180) > 0 ? -1 : 1;
      pace = POLLEN_PACE[0] + Math.min(1, (wind.spd || 0) / POLLEN_PACE_WIND_KMH) * POLLEN_PACE[1];
      // Pollen's light: from the sun when it is up, otherwise from the upper upwind corner.
      sunPoint = pos.el > 0 ? { x, y } : { x: travel > 0 ? W * .08 : W * .92, y: -H * .08 };
      const bx = sunPoint.x, by = pos.el > 0 ? Math.min(y, H * .2) : sunPoint.y, tx = W * (travel > 0 ? .62 : .38), length = Math.hypot(tx - bx, H - by) || 1;
      beam = { x: bx, y: by, dx: (tx - bx) / length, dy: (H - by) / length, length, width: Math.max(W, POLLEN_BEAM_MIN_PX) * POLLEN_BEAM_WIDTH };
      particles.length = 0; billows.length = 0; wall.length = 0; motes.length = 0; tufts.length = 0;
      // Stable distributions survive resize/redraw without re-electing or replaying anything.
      let seed = 109;
      const random = () => { seed = Math.imul(seed, 1664525) + 1013904223 | 0; return (seed >>> 0) / 4294967296; };
      for (let i = 0; i < PARTICLE_COUNT; i++) particles.push({ x: random(), y: random(), r: ASH_MIN_PX + random() * ASH_RANGE_PX,
        phase: random() * Math.PI * 2, speed: DRIFT_MIN_PX + random() * DRIFT_RANGE_PX + (wind.spd || 0) * WIND_SPEED_SCALE,
        front: i % FRONT_PARTICLE_EVERY === 0, tone: i % 3, poly: Array.from({ length: ASH_VERTICES }, () => ASH_POLY_MIN + random() * ASH_POLY_RANGE) });
      for (let i = 0; i < BILLOW_COUNT; i++) billows.push({ x: random(), y: random(), r: BILLOW_MIN + random() * BILLOW_RANGE, phase: random() * Math.PI * 2 });
      const m = Math.min(W, H), walls = SMOKE_WALL_PUFFS[W < PHONE_WIDTH ? 0 : 1];
      for (let i = 0; i < walls; i++) wall.push({ x: (i + random()) / walls * W * 1.3 - W * .15, r: m * (SMOKE_WALL_R[0] + random() * SMOKE_WALL_R[1]),
        rise: H * (SMOKE_WALL_RISE[0] + random() * SMOKE_WALL_RISE[1]), at: random() * FRONT_IN_MS, a: SMOKE_WALL_PUFF_ALPHA[0] + random() * SMOKE_WALL_PUFF_ALPHA[1],
        v: travel * W * (.05 + random() * .05) * pace, s: random() * 6 });
      const area = W * H, moteCount = Math.round(clamp(area / POLLEN_MOTE_AREA, ...POLLEN_MOTES)), tuftCount = Math.round(clamp(area / POLLEN_TUFT_AREA, ...POLLEN_TUFTS));
      for (let i = 0; i < moteCount; i++) {
        const z = random() ** 1.3, near = z > POLLEN_NEAR_Z[0];
        motes.push({ z, near, front: near || (z > POLLEN_FRONT_Z[0] && random() < POLLEN_FRONT_SHARE[0]), x0: random() * W * 1.3 - W * .15, y0: random() * H * 1.1 - H * .05,
          vx: travel * (POLLEN_MOTE_SPEED[0] + POLLEN_MOTE_SPEED[1] * z) * pace, vy: (random() - .5) * 6, wob: POLLEN_WOBBLE_PX[0] + POLLEN_WOBBLE_PX[1] * z,
          bf: .5 + random() * .7, ph: random() * 7, r: near ? POLLEN_NEAR_MOTE_PX[0] + random() * POLLEN_NEAR_MOTE_PX[1] : POLLEN_MOTE_PX[0] + z * POLLEN_MOTE_PX[1] });
      }
      for (let i = 0; i < tuftCount; i++) {
        const z = random() ** .85, near = z > POLLEN_NEAR_Z[1];
        tufts.push({ z, near, front: near || (z > POLLEN_FRONT_Z[1] && random() < POLLEN_FRONT_SHARE[1]), x0: random() * W * 1.3 - W * .15, y0: random() * H * 1.05 - H * .02,
          r: near ? POLLEN_NEAR_TUFT_PX[0] + random() * POLLEN_NEAR_TUFT_PX[1] : POLLEN_TUFT_PX[0] + z * POLLEN_TUFT_PX[1] + random() * POLLEN_TUFT_PX[2],
          vx: travel * (POLLEN_TUFT_SPEED[0] + POLLEN_TUFT_SPEED[1] * z) * pace, vy: (random() - .5) * 5, bob: POLLEN_BOB_PX[0] + POLLEN_BOB_PX[1] * z,
          bf: .4 + random() * .5, rot: random() * Math.PI * 2, om: (.12 + random() * .3) * (random() < .5 ? -1 : 1), ph: random() * 7, v: Math.floor(random() * FLUFF_SPRITES) });
      }
      motes.glint = motes.findIndex(p => p.front && !p.near && p.z > .5);
      exhaustSetup(road, api.windNow ? api.windNow() : wind, weather, pos);
    }
    function reset(layer) {
      const { c, ctx } = layer; ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, c.width, c.height);
      ctx.setTransform(c.width / W, 0, 0, c.height / H, 0, 0);
    }
    // The exhaust's colours follow the sky (and set the map plume's); its frame and strength exist while it reaches the home.
    function exhaustSetup(road, wind, weather, pos) {
      const app = typeof APP !== 'undefined' ? APP : {}, rain = clamp(weather.rain || 0);
      exhaustPal = exhaustPalette({ el: pos.el, cloud: weather.cloud, rain, thunder: weather.thunder }).pal;
      const value = `rgb(${over(exhaustPal.body[0], exhaustPal.edge[0], EXHAUST_MAP_EDGE).map(Math.round).join(' ')})`;
      if (html.style && html.style.getPropertyValue && html.style.getPropertyValue('--air-exhaust-map') !== value) {
        html.style.setProperty('--air-exhaust-map', value);
        if (typeof CustomEvent === 'function' && document.dispatchEvent) document.dispatchEvent(new CustomEvent('sky-exhaust-colour'));
      }
      if (!road) { plume = null; return; }
      const bearing = roadBearing(road.road, app.road && app.road.toward, wind.dir);
      plume = { ...plumeFrame({ bearing, windDir: wind.dir, windSpeed: wind.spd || 0, W, H }), rain,
        k: exhaustStrength({ strength: road.road && road.road.strength, pm: weather.pm, limits: app.limits, windSpeed: wind.spd || 0, rain }) };
      if (!soot.length) {
        let seed = 409;
        const R = () => { seed = Math.imul(seed, 1664525) + 1013904223 | 0; return (seed >>> 0) / 4294967296; };
        for (let i = 0; i < SOOT_COUNT[2]; i++) {
          const near = R() < SOOT_NEAR;
          soot.push({ d: R(), v: .6 + R() * .8, off: R() * 2 - 1, ph: R() * 7, wob: 2 + R() * 6, s: near ? 3.5 + R() * 3.5 : .9 + R() * 1.1, near, a: near ? .35 + R() * .25 : .55 + R() * .4 });
        }
      }
      if (!puffSprite && typeof document.createElement === 'function') {
        puffSprite = document.createElement('canvas'); puffSprite.width = puffSprite.height = 64; puffSprite.dataset.sprite = 'puff';
        const g = puffSprite.getContext('2d'), rg = g.createRadialGradient(32, 32, 0, 32, 32, 32);
        for (const [at, share] of PUFF_STOPS) rg.addColorStop(at, `rgb(255 255 255 / ${share})`);
        g.fillStyle = rg; g.fillRect(0, 0, 64, 64);
      }
    }
    const sootCount = () => Math.round(clamp(W * SOOT_COUNT[0], SOOT_COUNT[1], SOOT_COUNT[2]) * (.6 + .4 * plume.k));
    const sootFront = t => t < SOOT_FRONT[0] ? ease(t / SOOT_FRONT[0]) : t < SOOT_FRONT[1] ? 1 : (1 - clamp((t - SOOT_FRONT[1]) / SOOT_FRONT[2])) ** 2;
    // A speck rides the wind from the road's side across the screen, wobbling; the plume widens as it goes.
    function sootAt(p, t, slow = 1) {
      const sec = t / 1000, { origin: o, dx, dy, L, speed } = plume;
      const g = t < SOOT_GUST[1] ? 1 - (1 - t / SOOT_GUST[1]) ** 4 : 1;   // the opening's gust
      const u = wrapIn(p.d * 1.25 - .15 + p.v * speed * slow * sec / L, -.15, 1.1) - SOOT_GUST[0] * p.v * (1 - g);
      const spread = (.1 + .75 * Math.max(0, u)) * L * .5;
      const x = o.x + dx * u * L - dy * p.off * spread + Math.sin(sec * 1.3 + p.ph) * p.wob;
      let y = o.y + dy * u * L + dx * p.off * spread + Math.cos(sec * 1.1 + p.ph) * p.wob * .6;
      if (plume.rain) y = wrap(y + plume.rain * SOOT_RAIN_FALL * p.v * sec, H + 40) - 20;
      return [x, y, u];
    }
    const sootDim = (x, y, r) => SOOT_WORD_DIM + (1 - SOOT_WORD_DIM) * (wordsDim(x, y, r) - WORD_DIM) / (1 - WORD_DIM);
    function drawSoot(ctx, p, x, y, a) {
      const rgb = colour('exhaust', 'soot');
      if (p.near && puffSprite) { const r = p.s * 1.8; ctx.globalAlpha = clamp(a * .6); ctx.drawImage(tinted(puffSprite, rgb), x - r, y - r, r * 2, r * 2); ctx.globalAlpha = 1; return; }
      ctx.fillStyle = rgba(rgb, a); ctx.beginPath(); ctx.arc(x, y, p.s, 0, Math.PI * 2); ctx.fill();
    }
    // The opening: the specks pour in from the road's side and cross in front of the cards (half strength over words).
    function exhaustMatter(t, fade) {
      readWords(performance.now());
      const n = sootCount(), reach = 1.3 * ease(t / SOOT_ARRIVE_MS), F = sootFront(t);
      for (let i = 0; i < n; i++) {
        const p = soot[i], [x, y, u] = sootAt(p, t), arrive = smooth((reach - u) / .12);
        if (arrive <= 0) continue;
        const ahead = F > .01;
        const a = arrive * fade * Math.sqrt(plume.k) * p.a * (ahead ? F * sootDim(x, y, p.s + 1) : strength);
        if (a >= .02) drawSoot(ahead ? frontFine.ctx : backFine.ctx, p, x, y, a);
      }
    }
    // Settled: every third speck keeps drifting behind the glass, slower.
    function exhaustDrift(ctx, t, fade) {
      const a0 = fade * SOOT_SETTLED.alpha * Math.sqrt(plume.k);
      if (a0 < .01) return;
      readWords(performance.now());
      const n = sootCount();
      for (let i = 0; i < n; i += SOOT_SETTLED.every) {
        const p = soot[i], [x, y] = sootAt(p, t, SOOT_SETTLED.slow), a = a0 * p.a * wordsDim(x, y, p.s + 1);
        if (a >= .02) drawSoot(ctx, p, x, y, a);
      }
    }
    function stamp(ctx, x, y, r, rgb, a) { if (!puffSprite) return; ctx.globalAlpha = clamp(a); ctx.drawImage(tinted(puffSprite, rgb), x - r, y - r, r * 2, r * 2); ctx.globalAlpha = 1; }
    // The plume: a glow heaviest at the road's edge (behind the glass only) and billows riding the wind away from it,
    // wider and fainter as they go. Nothing sweeps across the screen.
    function exhaustHaze(ctx, alpha, t, isFront) {
      if (alpha < .003 || !plume) return;
      const a = alpha * plume.k, reach = ease(t / (isFront ? 900 : 1200)), { origin: o, dx, dy, L, speed } = plume, sec = t / 1000, m = Math.min(W, H);
      if (!isFront) {
        const wash = ctx.createLinearGradient(o.x, o.y, W - o.x, H - o.y);   // from the road's side to the opposite edge
        wash.addColorStop(0, rgba(colour('exhaust', 'body'), a * PLUME_WASH[0] * (.4 + .6 * reach)));
        wash.addColorStop(1, rgba(colour('exhaust', 'edge'), a * PLUME_WASH[1] * reach));
        ctx.fillStyle = wash; ctx.fillRect(0, 0, W, H);
        const rg = ctx.createRadialGradient(o.x, o.y, 0, o.x, o.y, L * PLUME_GLOW[2] * (.3 + .7 * reach));
        rg.addColorStop(0, rgba(colour('exhaust', 'foot'), a * PLUME_GLOW[0])); rg.addColorStop(.4, rgba(colour('exhaust', 'body'), a * PLUME_GLOW[1]));
        rg.addColorStop(1, rgba(colour('exhaust', 'edge'), 0)); ctx.fillStyle = rg; ctx.fillRect(0, 0, W, H);
      }
      for (let i = 0; i < PLUME_PUFFS; i++) {
        const u = wrap(i / PLUME_PUFFS + speed * sec / L * .8, 1);
        if (u > reach) continue;
        const side = Math.sin(i * 2.3) * (.05 + .3 * u) * L * .5, r = m * (.12 + .38 * u);
        stamp(ctx, o.x + dx * u * L * .9 - dy * side, o.y + dy * u * L * .9 + dx * side, r * 1.2, colour('exhaust', i % 2 ? 'body' : 'edge'), a * .55 * (1 - u) ** 1.3 * smooth(u / .08));
      }
    }
    function wash(ctx, top, bottom, alpha) {
      const g = ctx.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, rgba(top, alpha * SMOKE_TOP_ALPHA)); g.addColorStop(1, rgba(bottom, alpha));
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }
    // In front: an amber-grey wall rolls up from the horizon over everything, billows rising through it, then it fades behind the glass.
    function smokeFront(ctx, F, t) {
      const sec = t / 1000, edge = H * (SMOKE_WALL_FROM - SMOKE_WALL_TRAVEL * rollQ(t)), top = edge - H * SMOKE_WALL_SOFT;
      const fc = colour('smoke', 'front'), fl = colour('smoke', 'foot');
      const g = ctx.createLinearGradient(0, top, 0, H);
      g.addColorStop(0, rgba(fc, 0)); g.addColorStop(clamp(H * SMOKE_WALL_RAMP / (H - top + 1)), rgba(fc, SMOKE_WALL_ALPHA * F));
      g.addColorStop(1, rgba(fl, SMOKE_WALL_FOOT_ALPHA * F));
      ctx.fillStyle = g; ctx.fillRect(0, Math.max(0, top), W, H);
      const pale = over(fc, SMOKE_PALE_RGB, SMOKE_PALE_SHARE[0] + (SMOKE_PALE_SHARE[1] - SMOKE_PALE_SHARE[0]) * night), volume = colour('smoke', 'volume');
      for (const [i, b] of wall.entries()) {
        const y = H + b.r * .5 - b.rise * ease((t - b.at * .5) / SMOKE_WALL_RISE_MS) - sec * SMOKE_WALL_LIFT_PX;
        const x = b.x + b.v * sec + Math.sin(sec * SMOKE_WALL_SWAY_HZ + b.s) * SMOKE_WALL_SWAY_PX;
        puff(ctx, x, y, b.r * SMOKE_WALL_ASPECT[0], b.r * SMOKE_WALL_ASPECT[1], i % 3 === 0 ? pale : i % 3 === 1 ? volume : fl, b.a * F * (i % 3 === 0 ? SMOKE_WALL_PALE_ALPHA : SMOKE_WALL_DARK_ALPHA));
      }
    }
    function puff(ctx, x, y, rx, ry, rgb, a) {
      ctx.save(); ctx.translate(x, y); ctx.scale(rx, ry);
      const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
      for (const [at, share] of PUFF_STOPS) g.addColorStop(at, rgba(rgb, a * share));
      ctx.fillStyle = g; ctx.fillRect(-1, -1, 2, 2); ctx.restore();
    }
    function smoke(ctx, alpha, t, isFront) {
      const top = colour('smoke', isFront ? 'front' : 'top'), bottom = colour('smoke', isFront ? 'foot' : 'horizon');
      wash(ctx, top, bottom, alpha * SMOKE_WASH_ALPHA);
      const rise = (1 - clamp(t / BILLOW_RISE_MS)) ** 2;
      for (const [i, b] of billows.entries()) {
        const r = Math.max(W, H) * b.r, x = wrap(b.x * W + travel * t * BILLOW_DRIFT, W + r * 2) - r;
        const y = (b.y + BILLOW_START * (1 - b.y) * rise) * H + Math.sin(t / BILLOW_BOB_MS + b.phase) * BILLOW_BOB_PX;
        puff(ctx, x, y, r * BILLOW_ASPECT, r, i % 3 ? colour('smoke', 'volume') : top, alpha * BILLOW_ALPHA);
      }
      if (!isFront && sun) {
        puff(ctx, sun.x, sun.y, sun.r * SUN_REACH, sun.r * SUN_REACH, colour('smoke', 'glow'), alpha * SUN_GLOW_ALPHA);
        puff(ctx, sun.x, sun.y, sun.r * SUN_DISC_SCALE, sun.r * SUN_DISC_SCALE, colour('smoke', 'sun'), alpha * SUN_DISC_ALPHA);
      }
    }
    function drawGrain(ctx, kind, p, index, seconds, radius, tuftScale = 1) {
      if (kind === 'pollen') {
        // Callers set globalAlpha; these draw at the origin with that alpha.
        const a = ctx.globalAlpha;
        if (tuftScale && index === 0) drawFluff(ctx, p.phase % FLUFF_SPRITES | 0, (POLLEN_TUFT_PX[0] + p.r) * tuftScale, seconds * POLLEN_TUFT_SPIN, a, false);
        else drawMote(ctx, 0, 0, radius, a);
      } else if (kind === 'particles') {
        // The exhaust's soot, a little finer: dark specks on a light sky, light ones at night.
        ctx.globalAlpha *= FINE_SPECK_ALPHA; ctx.fillStyle = rgba(colour(kind, 'soot'), 1);
        ctx.beginPath(); ctx.arc(0, 0, radius * SOOT_SIZE * FINE_SPECK_SIZE, 0, Math.PI * 2); ctx.fill();
      } else if (kind === 'exhaust') {
        ctx.fillStyle = rgba(colour(kind, 'soot'), 1); ctx.beginPath(); ctx.arc(0, 0, radius * SOOT_SIZE, 0, Math.PI * 2); ctx.fill();
      } else {
        const rgb = colour(kind, 'ash' + p.tone), r = radius * (index % ASH_FEATURE_EVERY === 0 ? ASH_FEATURE_SCALE : 1);
        ctx.scale(1, ASH_FLAT + ASH_TUMBLE * Math.abs(Math.cos(seconds + p.phase))); ctx.fillStyle = rgba(rgb, 1); ctx.beginPath();
        for (let i = 0; i < ASH_VERTICES; i++) {
          const angle = i / ASH_VERTICES * Math.PI * 2;
          ctx.lineTo(Math.cos(angle) * r * p.poly[i], Math.sin(angle) * r * p.poly[i] * ASH_ASPECT);
        }
        ctx.closePath(); ctx.fill(); ctx.strokeStyle = rgba(over(rgb, colour(kind, 'rim'), ASH_RIM_BLEND), ASH_RIM_ALPHA);
        ctx.lineWidth = Math.max(ASH_RIM_MIN_PX, r * ASH_RIM_SHARE);
        ctx.beginPath(); ctx.ellipse(0, r * ASH_CURL_Y, r * ASH_CURL_RX, r * ASH_CURL_RY, 0, Math.PI * ASH_CURL_START, Math.PI * ASH_CURL_END); ctx.stroke();
      }
    }
    const grainRadius = (kind, p) => kind === 'pollen' ? POLLEN_MOTE_PX[0] + (p.r - ASH_MIN_PX) / ASH_RANGE_PX * POLLEN_MOTE_PX[1] : p.r;
    // A grain of dust: small and crisp, with a shadow just below on light skies (a lit grain, not a ring).
    function drawMote(ctx, x, y, r, a) {
      ctx.save(); ctx.globalAlpha = 1;
      if (!dark()) { ctx.fillStyle = rgba(POLLEN_MOTE_EDGE_RGB, a * POLLEN_MOTE_SHADOW_ALPHA); ctx.beginPath(); ctx.arc(x + POLLEN_MOTE_SHADOW_PX[0], y + POLLEN_MOTE_SHADOW_PX[1], r, 0, Math.PI * 2); ctx.fill(); }
      ctx.fillStyle = rgba(colour('pollen', 'dust'), a); ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
    // A cottonwood tuft at the current origin: its sprite, a warm-grey shade on light skies, and the darker seed at its root.
    function drawFluff(ctx, variant, r, rot, a, soft) {
      fluffSprites();
      const D = r * (soft ? 1.15 : 1) / FLUFF_RADIUS, squash = POLLEN_TUFT_SQUASH[0] + POLLEN_TUFT_SQUASH[1] * Math.cos(rot * 1.3 + variant);
      ctx.save(); ctx.rotate(rot); ctx.scale(1, squash);
      if (!dark()) { ctx.globalAlpha = clamp(a * POLLEN_TUFT_SHADE_ALPHA[soft ? 1 : 0]); ctx.drawImage(tinted(sprites.soft[variant], POLLEN_SHADE_RGB), -D / 2 + r * POLLEN_TUFT_SHADE_OFFSET[0], -D / 2 + r * POLLEN_TUFT_SHADE_OFFSET[1], D, D); }
      ctx.globalAlpha = clamp(a); ctx.drawImage(tinted((soft ? sprites.soft : sprites.fluff)[variant], colour('pollen', 'fluff')), -D / 2, -D / 2, D, D);
      const [sx, sy] = sprites.seed[variant];
      ctx.globalAlpha = clamp(a * (soft ? .45 : .9)); ctx.fillStyle = rgba(over(POLLEN_SEED_RGB, [40, 30, 20], night * .4), 1);
      ctx.beginPath(); ctx.ellipse(sx * D, sy * D, Math.max(.7, r * .1), Math.max(.5, r * .06), 0, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
    const moteAt = (p, t) => { const s = t / 1000;   // across with the wind, slowly rising and sinking
      return [wrapIn(p.x0 + p.vx * s + Math.sin(s * 1.3 + p.ph) * p.wob * .4, -POLLEN_WRAP_PX[0], W + POLLEN_WRAP_PX[0]),
        wrapIn(p.y0 + p.vy * s + Math.sin(s * p.bf + p.ph * 2) * p.wob, -POLLEN_WRAP_PX[0], H + POLLEN_WRAP_PX[0])]; };
    const tuftAt = (q, t) => { const s = t / 1000;
      return [wrapIn(q.x0 + q.vx * s + Math.sin(s * .8 + q.ph) * POLLEN_TUFT_SWAY_PX, -POLLEN_WRAP_PX[1], W + POLLEN_WRAP_PX[1]),
        wrapIn(q.y0 + q.vy * s + Math.sin(s * q.bf + q.ph) * q.bob, -POLLEN_WRAP_PX[1], H + POLLEN_WRAP_PX[1]), q.rot + q.om * s]; };
    const beamAt = (x, y) => {
      const px = x - beam.x, py = y - beam.y, along = px * beam.dx + py * beam.dy, across = px * beam.dy - py * beam.dx;
      return along < 0 ? 0 : Math.exp(-2 * (across / beam.width) ** 2);
    };
    // The pass: fine dust and tufts drift behind the glass, and the front ones drift over everything while the front lasts (no veil).
    // The front grains stay above the cards for the whole pass (the prototype drew them in the glass), dimmed over words.
    function pollenMatter(t, A, F, P) {
      const back = backFine.ctx, ahead = frontFine.ctx, glass = POLLEN_GLASS_THEME[dark() ? 1 : 0];
      readWords(performance.now());
      for (const p of motes) {
        const [x, y] = moteAt(p, t);
        if (p.front) {
          const a = (F * (.6 + .4 * p.z) + (1 - F) * A * P * (.45 + .55 * p.z)) * wordsDim(x, y, p.r + 1);
          if (a >= .02) drawMote(ahead, x, y, p.r, a);
          continue;
        }
        // The prototype also drew the nearer dust in each card's glass; above the cards, dimmed over words, it reads the same.
        if (p.z >= POLLEN_GLASS_MIN_Z) {
          const a = A * P * (.55 + .45 * p.z) * glass * wordsDim(x, y, p.r + 1);
          if (a >= .02) drawMote(ahead, x, y, p.r, a);
          continue;
        }
        const a = A * P * (.45 + .55 * p.z);
        if (a >= .02) drawMote(back, x, y, p.r, a * (.65 + .35 * beamAt(x, y)));
      }
      const glint = motes[motes.glint], k = glint ? passGlint(t) : 0;
      if (k > .02) {
        const [x, y] = moteAt(glint, t), r = POLLEN_GLINT_PX + POLLEN_GLINT_GROW_PX * k;
        puff(F > .01 ? ahead : back, x, y, r, r, colour('pollen', 'glint'), .7 * k * (F > .01 ? F : A * P));
      }
      for (const q of tufts) {
        const [x, y, rot] = tuftAt(q, t);
        if (q.front) {
          const a = (F * (q.near ? .8 : .95) + (1 - F) * A * P * (.55 + .45 * q.z)) * wordsDim(x, y, q.r * 1.3);
          if (a >= .02) { ahead.save(); ahead.translate(x, y); drawFluff(ahead, q.v, q.r, rot, a, false); ahead.restore(); }
          continue;
        }
        const a = A * P * POLLEN_GLASS_TUFT * glass * wordsDim(x, y, q.r * 1.3);
        if (a < .02) continue;
        ahead.save(); ahead.translate(x, y); drawFluff(ahead, q.v, q.r, rot, a, q.z < .25); ahead.restore();
      }
    }
    const passGlint = t => { const u = t - POLLEN_GLINT_AT_MS;
      return u < 0 ? 0 : u < POLLEN_GLINT_IN_MS ? ease(u / POLLEN_GLINT_IN_MS) : u < POLLEN_GLINT_OUT_MS ? 1 : 1 - smooth((u - POLLEN_GLINT_OUT_MS) / (POLLEN_GLINT_END_MS - POLLEN_GLINT_OUT_MS)); };
    // Settled: a share of the motes and the larger tufts keep floating behind the glass at half speed, brighter in the sunbeam.
    // During the pass the same grains fade in at the same places, so the pass hands over to the drift without a jump.
    // Kinds with a settled drift behind the glass: pollen and fine particles.
    const driftKinds = () => conditions.map(c => c.kind).filter(kind => kind === 'pollen' || kind === 'particles' || kind === 'exhaust');
    function wantsDrift() { return !destroyed && !paused && !motionStill && !reduced() && driftKinds().length > 0 && !veiled(); }
    function driftSet() {
      const count = POLLEN_SETTLE_TUFTS[W < PHONE_WIDTH ? 0 : 1];
      return { motes: motes.filter((p, i) => !p.near && i % POLLEN_SETTLE_EVERY === 0), tufts: tufts.filter(q => !q.near).sort((a, b) => b.z - a.z).slice(0, count) };
    }
    function paintDrift() {
      const { ctx } = backDrift; reset(backDrift);
      // Reduced motion: fine particles keep a still of their specks (the haze alone would read as weather, not particles).
      if (!drift) {
        if (motionStill && primary === 'particles' && !veiled()) fineSpecks(ctx, MOTION_MS, 1);
        if (motionStill && plume && primary === 'exhaust' && !veiled()) exhaustDrift(ctx, MOTION_MS, 1);
        return;
      }
      if (driftKinds().includes('particles')) {
        const passT = run && !run.skip && run.kind === 'particles' ? age : null;
        const t = passT ?? MOTION_MS + drift.el * FINE_SPECK_SLOW;
        const fade = passT != null ? smooth((passT - PARTICLE_FADE_AT_MS) / POLLEN_SETTLE_IN_MS) : drift.from + (1 - drift.from) * smooth(drift.el / POLLEN_SETTLE_IN_MS);
        fineSpecks(ctx, t, fade);
      }
      if (plume && driftKinds().includes('exhaust')) {
        const passT = run && !run.skip && run.kind === 'exhaust' ? age : null;
        const t = passT ?? MOTION_MS + drift.el * SOOT_SETTLED.slow;
        const fade = passT != null ? smooth((passT - PARTICLE_FADE_AT_MS) / POLLEN_SETTLE_IN_MS) : drift.from + (1 - drift.from) * smooth(drift.el / POLLEN_SETTLE_IN_MS);
        exhaustDrift(ctx, t, fade);
      }
      if (driftKinds().includes('pollen')) paintPollenDrift(ctx);
    }
    function paintPollenDrift(ctx) {
      const passT = run && !run.skip && run.kind === 'pollen' ? age : null;
      const t = passT ?? MOTION_MS + drift.el * POLLEN_SETTLE_SLOW;
      const fade = passT != null ? smooth((passT - PARTICLE_FADE_AT_MS) / POLLEN_SETTLE_IN_MS) : drift.from + (1 - drift.from) * smooth(drift.el / POLLEN_SETTLE_IN_MS);
      if (fade < .01) return;
      const grow = 1 + (POLLEN_SETTLE_GROW - 1) * fade, set = driftSet(), theme = POLLEN_SETTLE_THEME[dark() ? 1 : 0];
      readWords(performance.now());
      for (const p of set.motes) {
        const [x, y] = moteAt(p, t), a = fade * theme * (.45 + .55 * beamAt(x, y)) * (.6 + .4 * p.z) * wordsDim(x, y, p.r + 1);
        if (a < .02) continue;
        drawMote(ctx, x, y, p.r, a);
      }
      for (const q of set.tufts) {
        const [x, y, rot] = tuftAt(q, t), r = q.r * grow, a = fade * theme * (.75 + .25 * beamAt(x, y)) * wordsDim(x, y, r * 1.3);
        if (a < .02) continue;
        ctx.save(); ctx.translate(x, y); drawFluff(ctx, q.v, r, rot, a, false); ctx.restore();
      }
    }
    function matter(kind, t, fade) {
      if (kind === 'exhaust') { if (plume) exhaustMatter(t, fade); return; }
      const seconds = t / 1000;
      for (const [index, p] of particles.entries()) {
        const ahead = p.front && (run?.skip ? run.skip.front > 0 : front > 0), ctx = (ahead ? frontFine : backFine).ctx;
        const a = fade * (ahead ? front : strength * (p.front ? 1 - front : 1)) * PARTICLE_ALPHA;
        if (!a) continue;
        let [x, y] = kind === 'particles' ? speckAt(p, t) : [wrap(p.x * W + travel * p.speed * seconds, W + PARTICLE_MARGIN * 2) - PARTICLE_MARGIN,
          wrap(p.y * H + Math.sin(seconds + p.phase) * PARTICLE_BOB_PX, H)];
        ctx.save(); ctx.translate(x, y); ctx.rotate(p.phase + seconds * ASH_SPIN);
        ctx.globalAlpha = a;
        drawGrain(ctx, kind, p, index, seconds, grainRadius(kind, p));
        ctx.restore();
      }
    }
    const sparseGrains = kinds => kinds.length ? Array.from({ length: SPARSE_PARTICLE_MAX }, (_, index) => ({ kind: kinds[index % kinds.length], index })) : [];
    function sparseMatter(grains, t, skipFade = 1) {
      const pollenFeature = grains.find(g => g.kind === 'pollen');
      for (const { kind, index: i } of grains) {
        const tuft = kind === 'pollen' && i === pollenFeature.index;
        const p = particles[tuft ? 0 : Math.floor((i + .5) * particles.length / SPARSE_PARTICLE_MAX)];
        const elapsed = t - i / SPARSE_PARTICLE_MAX * SPARSE_STAGGER_MS;
        const fade = smooth(elapsed / SPARSE_FADE_MS) * (1 - smooth((elapsed - SPARSE_DURATION_MS + SPARSE_FADE_MS) / SPARSE_FADE_MS)) * skipFade;
        const ctx = backFine.ctx, seconds = Math.max(0, elapsed) / 1000;
        const x = wrap(p.x * W + travel * p.speed * seconds, W + PARTICLE_MARGIN * 2) - PARTICLE_MARGIN;
        const y = wrap(p.y * H + Math.sin(seconds + p.phase) * PARTICLE_BOB_PX, H);
        ctx.save(); ctx.translate(x, y); ctx.rotate(p.phase + seconds * ASH_SPIN);
        ctx.globalAlpha = fade * SPARSE_BACK_ALPHA;
        // At most one sparse tuft; the rest are fine dust.
        drawGrain(ctx, kind, p, tuft ? 0 : i, seconds, grainRadius(kind, p), tuft ? 1 : 0); ctx.restore();
      }
    }
    // Pollen's sky: a faint spring haze (the blue stays, so white dust shows), the sun's glare, and the cream sunbeam.
    function pollenSky(ctx, strength) {
      const theme = dark() ? 1 : 0, glare = colour('pollen', 'glare'), r = Math.max(W, H) * POLLEN_GLARE_REACH;
      wash(ctx, colour('pollen', 'wash'), colour('pollen', 'wash'), strength * POLLEN_WASH_ALPHA * (1 - POLLEN_NIGHT_WASH * night));
      const g = ctx.createRadialGradient(sunPoint.x, sunPoint.y, 0, sunPoint.x, sunPoint.y, r);
      g.addColorStop(0, rgba(glare, POLLEN_GLARE_ALPHA[theme] * strength * (1 - (1 - POLLEN_NIGHT_GLARE) * night))); g.addColorStop(1, rgba(glare, 0));
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      const a = POLLEN_BEAM_ALPHA[theme] * strength * (1 - POLLEN_NIGHT_BEAM * night);
      if (a < .003) return;
      ctx.save(); ctx.translate(beam.x, beam.y); ctx.rotate(Math.atan2(beam.dy, beam.dx) - Math.PI / 2);
      const bg = ctx.createLinearGradient(-beam.width * 1.6, 0, beam.width * 1.6, 0), rgb = colour('pollen', 'beam');
      // exp(-2 (across / width)²), sampled.
      for (const [u, v] of POLLEN_BEAM_STOPS) bg.addColorStop(u, rgba(rgb, a * v));
      ctx.fillStyle = bg; ctx.fillRect(-beam.width * 1.6, 0, beam.width * 3.2, beam.length * 1.4); ctx.restore();
    }
    // Fine particles: smoke's wash and volumes, even across the sky and barely moving, in a neutral grey-beige.
    function fineHaze(ctx, alpha, t, isFront) {
      wash(ctx, colour('particles', isFront ? 'front' : 'top'), colour('particles', isFront ? 'foot' : 'horizon'), alpha * (isFront ? FINE_FRONT_ALPHA : FINE_WASH_ALPHA));
      const volume = colour('particles', 'volume'), light = colour('particles', isFront ? 'front' : 'top');
      for (const [i, b] of billows.entries()) {
        const r = Math.max(W, H) * b.r, x = wrap(b.x * W + travel * t * BILLOW_DRIFT * FINE_BILLOW_DRIFT, W + r * 2) - r;
        const y = b.y * H + Math.sin(t / BILLOW_BOB_MS + b.phase) * BILLOW_BOB_PX;
        puff(ctx, x, y, r * BILLOW_ASPECT, r, i % 3 ? volume : light, alpha * (isFront ? FINE_FRONT_BILLOW_ALPHA : FINE_BILLOW_ALPHA));
      }
    }
    // Fine particles' settled specks: a third of the pass's soot, still wandering behind the glass.
    function fineSpecks(ctx, t, fade) {
      const alpha = fade * FINE_SETTLE_SPECK_ALPHA[dark() ? 1 : 0] * FINE_SPECK_ALPHA;
      if (alpha < .01) return;
      readWords(performance.now());
      for (const [index, p] of particles.entries()) {
        if (index % FINE_SETTLE_SPECK_EVERY) continue;
        const [x, y] = speckAt(p, t), a = alpha * wordsDim(x, y, p.r + 1);
        if (a < .02) continue;
        ctx.save(); ctx.translate(x, y); ctx.globalAlpha = a; drawGrain(ctx, 'particles', p, index, t / 1000, p.r); ctx.restore();
      }
    }
    const speckAt = (p, t) => { const seconds = t / 1000;
      return [wrap(p.x * W + travel * p.speed * FINE_SPECK_DRIFT * seconds, W + PARTICLE_MARGIN * 2) - PARTICLE_MARGIN, wrap(p.y * H + Math.sin(seconds + p.phase) * PARTICLE_BOB_PX, H)]; };
    function drawWash(kind, strength, age) {
      if (!strength) return;
      if (kind === 'smoke') smoke(backSoft.ctx, strength, age, false);
      else if (kind === 'exhaust') {
        const hold = holdOf('exhaust'), sky = strength >= hold ? EXHAUST_SKY_HOLD + (strength - hold) * (1 - EXHAUST_SKY_HOLD) / (1 - hold) : strength * EXHAUST_SKY_HOLD / hold;
        exhaustHaze(backSoft.ctx, sky, age, false);
      }
      else if (kind === 'particles') fineHaze(backSoft.ctx, strength, age, false);
      else if (kind === 'pollen') pollenSky(backSoft.ctx, strength);
    }
    // The shown kind can change while a stopped pass fades (the conditions changed): keep the outgoing cast until its fade is done.
    const outgoing = () => run?.skip && run.kind !== primary ? run : null;
    function draw() {
      if (destroyed || paused) return;
      if (api.prepareInk) api.prepareInk();
      geometry(); for (const l of layers) reset(l);
      const active = AIR_KINDS.includes(primary);
      const leaving = outgoing(), share = leaving?.skip.fade ?? 0;
      // Fade the native sun opposite the smoke wash, which draws its own smaller sun.
      const cover = primary === 'smoke' && !veiled() ? 1 - share : leaving?.kind === 'smoke' ? share : 0;
      if (cover !== sunCover) { sunCover = cover; html.style.setProperty('--sky-smoke-cover', String(cover)); }
      html.classList.toggle('sky-outdoor-air', active);
      html.classList.toggle('sky-smoke-air', cover > 0);
      shownStrength = active ? primaryStrength() : 0;
      if (active) {
        drawWash(primary, primaryStrength() * (1 - share), primaryAge());
        if (leaving) drawWash(leaving.kind, leaving.skip.strength * share, leaving.skip.age);
        if (front > 0 && (leaving || run?.kind === primary)) {
          if (run?.kind === 'smoke') smokeFront(frontSoft.ctx, front, age);
          if (run?.kind === 'exhaust') exhaustHaze(frontSoft.ctx, front * EXHAUST_FRONT_ALPHA, age, true);
          if (run?.kind === 'particles') fineHaze(frontSoft.ctx, front, age, true);
        }
        const skipFade = run?.skip?.fade ?? 1;
        const fade = run ? (1 - smooth((age - PARTICLE_FADE_AT_MS) / (MOTION_MS - PARTICLE_FADE_AT_MS))) * skipFade : 0;
        if (fade > 0 && run.kind === 'pollen') pollenMatter(age, strength, front, fade);
        else if (fade > 0) matter(run.kind, age, fade);
        if (sparse) sparseMatter(sparse.grains, sparse.age, sparse.skip?.fade ?? 1);
      }
      paintDrift();
      for (let i = 0; i < samples.length; i++) {
        const s = samples[i], soft = i ? frontSoft : backSoft, fine = i ? frontFine : backFine;
        s.ctx.clearRect(0, 0, s.c.width, s.c.height);
        s.ctx.drawImage(soft.c, 0, 0, s.c.width, s.c.height); s.ctx.drawImage(fine.c, 0, 0, s.c.width, s.c.height);
        s.pixels = s.ctx.getImageData(0, 0, s.c.width, s.c.height);
      }
      onDraw();
      if (api.invalidateInk) api.invalidateInk();
    }
    // The settled drift alone: its own layer at its own frame rate, no ink or glass work.
    function stepDrift(now) {
      if (!wantsDrift()) { if (drift) { drift = null; paintDrift(); } return false; }
      if (!drift) drift = { start: now, el: 0, last: -Infinity, from: 0 };
      // A pollen pass paints the drift with its own frames; the settled drift then continues from where the pass left it.
      if (run && !run.skip && (run.kind === 'pollen' || run.kind === 'particles' || run.kind === 'exhaust')) { drift.start = now; drift.from = smooth((MOTION_MS - PARTICLE_FADE_AT_MS) / POLLEN_SETTLE_IN_MS); return false; }
      drift.el = now - drift.start;
      if (now - drift.last >= 1000 / POLLEN_SETTLE_FPS - 2) { drift.last = now; return true; }
      return false;
    }
    function tick(now) {
      frame = 0; if (paused || destroyed) return;
      const animating = !!(run || sparse);   // a pass or burst that ends on this frame still draws its settled frame
      if (run) {
        if (run.skip) {
          age = run.skip.age;
          const q = smooth((now - run.skip.at) / SKIP_FADE_MS);
          run.skip.fade = 1 - q;
          strength = run.skip.strength + (holdOf(run.kind) - run.skip.strength) * q;
          cast = run.skip.cast * (1 - q);
          front = run.skip.front * (1 - smooth((now - run.skip.at) / run.skip.frontMs));
          if (run.skip.age + (now - run.skip.at) >= frontEnd(run.kind)) report(null);
          if (now - run.skip.at >= SKIP_FADE_MS) finish();
        } else {
          // The pass's clock starts on its first painted frame and never jumps more than one step, so a stalled first load still shows it.
          if (run.last == null) run.last = now;
          run.clock += Math.min(MAX_PASS_STEP_MS, Math.max(0, now - run.last)); run.last = now;
          age = Math.min(MOTION_MS, run.clock);
          strength = passStrength(age, run.from, run.kind);
          cast = passCast(age, run.castFrom);
          front = passFront(age, run.kind);
          if (age >= frontEnd(run.kind)) report(null);
          if (age >= MOTION_MS) finish();
        }
      }
      if (sparse) {
        if (sparse.skip) {
          sparse.skip.fade = 1 - smooth((now - sparse.skip.at) / SKIP_FADE_MS);
          if (!sparse.skip.fade) sparse = null;
        } else {
          sparse.age = now - sparse.start;
          if (sparse.age >= SPARSE_DURATION_MS + SPARSE_STAGGER_MS) sparse = null;
        }
      }
      const drifting = stepDrift(now), full = animating || frameDue;
      frameDue = false;
      if (full) draw();
      else if (drifting) paintDrift();
      if (run || sparse || drift) schedule(full || !!(run || sparse));
    }
    const primaryStrength = () => run && run.kind === primary ? strength : veiled() ? 0 : holdOf(primary);
    // The run whose colour the cards show: the leader's pass, or an outgoing one while its skip fades. Settled, none.
    const glassRun = () => run && (run.kind === primary || run.skip) && palette[run.kind] ? run : null;
    const primaryAge = () => run && run.kind === primary ? age : MOTION_MS;
    function finish() {
      run = null; age = MOTION_MS; front = 0; strength = holdOf(primary); cast = 0; report(null);
    }
    function settle() { motionStill = false; cancel(); sparse = null; finish(); draw(); if (wantsDrift()) schedule(false); }
    function redraw() { if (destroyed) return; readPalette(); geometryDirty = true; draw(); if (wantsDrift()) schedule(false); }
    function resize() {
      if (destroyed) return;
      if (paused) { resizeDue = true; return; }
      resizeDue = false;
      ({ W, H, dpr } = api.size()); W = Math.max(1, W); H = Math.max(1, H); dpr = Math.min(CRISP_DPR_MAX, dpr || 1);
      for (const layer of layers) {
        const scale = layer.scale === 1 ? Math.min(dpr, Math.sqrt(CRISP_PIXEL_MAX / (W * H))) : layer.scale;
        layer.c.width = Math.max(1, Math.ceil(W * scale)); layer.c.height = Math.max(1, Math.ceil(H * scale));
      }
      for (const s of samples) { s.c.width = backSoft.c.width; s.c.height = backSoft.c.height; }
      redraw();
    }
    const onScroll = () => { geometryDirty = true; frameDue = true; words = null; schedule(); };
    window.addEventListener('scroll', onScroll, { passive: true });
    resize();
    return {
      setConditions(next, owner) {
        if (destroyed) return;
        conditions = next.filter(c => AIR_KINDS.includes(c.kind));
        primary = conditions.some(c => c.kind === owner) ? owner : conditions[0]?.kind || null;
        if (run && !conditions.some(c => c.kind === run.kind)) { cancel(); finish(); }
        if (sparse) {
          sparse.grains = sparse.grains.filter(p => conditions.some(c => c.kind === p.kind));
          if (!sparse.grains.length) { sparse = null; if (!run) cancel(); }
          else schedule();
        }
        redraw();
      },
      play(kind) {
        if (destroyed || paused || !conditions.some(c => c.kind === kind)) return;
        if (reduced()) { settle(); return; }
        // The colour rises from what is on screen now: nothing while the load pass was awaited, else the held cast.
        const from = primary === kind ? shownStrength : 0, castFrom = glassRun()?.kind === kind ? cast : 0;
        cancel(); sparse = null; motionStill = false; age = 0; strength = from; front = 0; cast = castFrom;
        run = { kind, clock: 0, last: null, from, castFrom, skip: null }; report(kind); draw(); schedule();
      },
      settle, still() { settle(); motionStill = true; paintDrift(); },
      stop() {
        if (destroyed) return;
        const now = performance.now();
        if (sparse && !sparse.skip) sparse.skip = { at: now, fade: 1 };
        if (!run) { if (sparse) schedule(); else { cancel(); draw(); } return; }
        if (!run.skip) {
          run.skip = { at: now, age, fade: 1, strength, cast, front, frontMs: Math.min(SKIP_FADE_MS, Math.max(1, frontEnd(run.kind) - run.clock)) };
        }
        schedule();
      },
      // The controller owns the 20–40 s cadence; this event is finite and never calls play/onPlaying.
      burst(kinds = conditions.map(c => c.kind)) {
        if (destroyed || paused || reduced() || motionStill || run || sparse) return;
        const active = [...new Set(kinds)].filter(kind => conditions.some(c => c.kind === kind));
        if (!active.length) return;
        sparse = { grains: sparseGrains(active), start: performance.now(), age: 0 }; draw(); schedule();
      },
      clear() { if (destroyed) return; conditions = []; primary = null; geometryDirty = true; settle(); },
      setPaused(hidden) { paused = hidden; if (hidden) { cancel(); sparse = null; finish(); } else if (resizeDue) resize(); else redraw(); },
      resize, redraw,
      invalidateGeometry() { geometryDirty = true; frameDue = true; schedule(); },
      // Each condition's full tint, the most of the air's colour a pass can lend the glass; the solver bounds every card's share of it.
      airTints() { return conditions.map(({ kind }) => palette[kind].tint); },
      // The air's own glass opacity: each kind's glass at full strength, verified by the solver before it is used.
      airAlphaMax() { const theme = dark() ? 1 : 0; return Math.max(0, ...conditions.map(c => AIR_GLASS_ALPHA[c.kind][theme])); },
      // How far the pass has taken the cards from the everyday glass toward their verified air material: 0 once settled.
      glassCast() { return glassRun() ? cast : 0; },
      // The pass's air colour for the glass (the shown kind's tint), or null when no pass lends one.
      airTint() { const shown = glassRun(); return shown ? palette[shown.kind].tint : null; },
      materialColor(base) {
        const shown = glassRun();
        return shown ? over(base, palette[shown.kind].tint, cast) : base;
      },
      get smokeCover() { return sunCover; },
      get frontActive() { return front > 0; },
      sampleBackground(rgb, x, y, layer = 'all') {
        for (const [index, s] of samples.entries()) {
          if (layer === 'rear' && index !== 0 || layer === 'front' && index !== 1) continue;
          if (!s.pixels) continue;
          const i = (clamp(Math.floor(y * s.c.height / H), 0, s.c.height - 1) * s.c.width + clamp(Math.floor(x * s.c.width / W), 0, s.c.width - 1)) * 4;
          const data = s.pixels.data; rgb = over(rgb, [data[i], data[i + 1], data[i + 2]], data[i + 3] / 255);
        }
        return rgb;
      },
      destroy() {
        destroyed = true; cancel(); report(null); window.removeEventListener('scroll', onScroll);
        for (const l of layers) { l.c.remove(); l.c.width = l.c.height = 1; }
        for (const s of samples) { s.pixels = null; s.c.width = s.c.height = 1; }
        html.classList.remove('sky-outdoor-air', 'sky-smoke-air'); html.style.removeProperty('--sky-smoke-cover');
      },
    };
  }

  function create(api) {
    const html = document.documentElement;
    const filters = CSS.supports('backdrop-filter', 'blur(1px)') || CSS.supports('-webkit-backdrop-filter', 'blur(1px)');
    let destroyed = false, paused = document.hidden, frame = 0, allDirty = true, materialDirty = true, membershipDirty = true, resetInk = false;
    const materials = new Map(), locations = new Map(), text = new Map(), observed = new Set(), dirty = new Set(), repaired = new Set(), liveAlpha = new Map();
    html.classList.add('sky-glass');

    let outdoor = null, materialSaturation = 1;

    function skySamples() {
      const sky = api.sky();
      return [sky.top, sky.hor, ...SKY_ENDPOINTS].filter(Boolean);
    }
    const fading = node => typeof node.getAnimations === 'function' && node.getAnimations().some(a => a.playState === 'running');
    // Solve a card again once its own animations end, so the cached material matches what stays on screen.
    const awaiting = new Set();
    function solveAfterAnimations(el) {
      if (awaiting.has(el) || typeof el.getAnimations !== 'function') return;
      const running = el.getAnimations({ subtree: true }).filter(a => a.playState === 'running' && Number.isFinite(a.effect?.getComputedTiming?.().endTime));
      if (!running.length) return;
      awaiting.add(el);
      Promise.all(running.map(a => a.finished.catch(() => {}))).then(() => { awaiting.delete(el); if (!destroyed) { dirty.add(el); schedule(); } });
    }
    function readText(el) {
      const entries = [], styles = new Map();
      const styleFor = node => { if (!styles.has(node)) styles.set(node, getComputedStyle(node)); return styles.get(node); };
      // These readouts sit directly on the glass. Budget for both inks before scrubbing,
      // independent of the current reading or warning class.
      for (const node of el.querySelectorAll(SCRUB_READOUT)) {
        const style = styleFor(node);
        for (const token of ['--soft', '--warn']) {
          entries.push({ node, rgb: parse(style.getPropertyValue(token)), alpha: 1, path: [] });
        }
      }
      for (const node of [el, ...el.querySelectorAll('*')]) {
        if (!Array.from(node.childNodes).some(n => n.nodeType === 3 && n.textContent.trim())) continue;
        if (node.closest(TRANSIENT) || /^(TITLE|SCRIPT|STYLE)$/.test(node.tagName)) continue;
        if (!node.getClientRects().length || (node.closest('[aria-hidden="true"]') && !node.matches('.sx-tl text, svg.ts text'))) continue;
        const style = styleFor(node), rgb = parse(node instanceof SVGElement ? style.fill : style.color);
        if (!rgb) continue;
        let hidden = false;
        const path = [];
        for (let p = node; p && p !== el; p = p.parentElement) {
          const s = styleFor(p);
          // A fade-in caught mid-way is read at its end state: its momentary opacity would fail every tint and cache the
          // opaque fallback (white, or black in the dark theme) for the whole session. The card is solved again when it ends.
          const opacity = fading(p) ? 1 : Number(s.opacity);
          if (s.display === 'none' || s.visibility !== 'visible' || opacity === 0) { hidden = true; break; }
          path.unshift({ node: p, rgb: parse(s.backgroundColor), opacity });
        }
        if (hidden) continue;
        // SVG label halos are an inner surface too, and are painted before the ink.
        if (node instanceof SVGElement && style.paintOrder.startsWith('stroke') && style.stroke !== 'none') {
          const stroke = parse(style.stroke);
          if (stroke) { stroke[3] = (stroke[3] ?? 1) * Number(style.strokeOpacity); path.push({ node: null, rgb: stroke, opacity: 1 }); }
        }
        entries.push({ node, rgb: rgb.slice(0, 3), alpha: (rgb[3] ?? 1) * (node instanceof SVGElement ? Number(style.fillOpacity) : 1),
          svgFilm: node instanceof SVGElement, path });
      }
      return entries;
    }
    // The everyday glass is the same solve as on a clean-air day. A pass then lends it the air's colour up to airShare of each
    // condition's full tint (airTints), at its alpha and up to airAlpha, wherever its inks still read at MIN_CONTRAST.
    function solve(samples, tint, entries, brightness, saturation, safe, filmRgb, filmAlpha, airTints = [], airMax = 0) {
      const backgrounds = filters ? samples.map(c => frost(c, brightness, saturation)) : samples;
      // HTML films are in each text path. SVG films are sibling shapes, so also test their conservative bound.
      const entryPasses = (entry, bg, target) => (entry.svgFilm ? [bg, over(bg, filmRgb, filmAlpha)] : [bg])
        .every(surface => textContrast(entry, surface) >= target);
      // The solver stays off the animation path: a pass only blends the verified ends.
      const passes = (rgb, alpha, target, inks = entries) => backgrounds.every(bg =>
        inks.every(entry => entryPasses(entry, over(bg, rgb, alpha), target)));
      // A pass moves the colour and the alpha together (cast 0 → 1): check its middle and its peak for every air tint.
      const withAir = (m, inks = entries) => {
        if (!airTints.length) return m;
        const reads = (share, alpha, target = MIN_CONTRAST) => airTints.every(air => [.5, 1].every(c =>
          passes(over(m.rgb, air, share * c), m.alpha + (alpha - m.alpha) * c, target, inks)));
        let alpha = null;
        if (filters && reads(1, 1, TARGET_CONTRAST)) {
          let lo = m.alpha, hi = 1;
          for (let i = 0; i < SEARCH_STEPS; i++) { const mid = (lo + hi) / 2; if (reads(1, mid, TARGET_CONTRAST)) hi = mid; else lo = mid; }
          if (reads(1, Math.min(1, hi + ALPHA_MARGIN))) alpha = Math.min(1, hi + ALPHA_MARGIN);
        }
        if (alpha == null && reads(1, 1)) alpha = 1;
        if (alpha != null) {
          // The air may make the glass more opaque, up to its kind's level, only where every ink still passes there too.
          m.airShare = 1; m.airAlpha = airMax > alpha && reads(1, airMax) ? airMax : alpha;
          return m;
        }
        // Inks too faint for the full tint even on opaque glass: the largest share they read on.
        let lo = 0, hi = 1;
        for (let i = 0; i < SEARCH_STEPS; i++) { const mid = (lo + hi) / 2; if (reads(mid, 1)) lo = mid; else hi = mid; }
        m.airShare = lo; m.airAlpha = 1;
        return m;
      };
      const material = (rgb, alpha, repairs = [], opacityNodes = new Set()) => ({ rgb: rgb.slice(0, 3), alpha, airAlpha: alpha, airShare: 0,
        backdropBrightness: filters && alpha < 1 ? brightness : 1, repairs, opacityNodes });
      if (filters && passes(tint, 1, TARGET_CONTRAST)) {
        let lo = 0, hi = 1;
        for (let i = 0; i < SEARCH_STEPS; i++) {
          const mid = (lo + hi) / 2;
          if (passes(tint, mid, TARGET_CONTRAST)) hi = mid; else lo = mid;
        }
        const alpha = Math.min(1, Math.max(MIN_ALPHA, hi + ALPHA_MARGIN));
        if (passes(tint, alpha, MIN_CONTRAST)) return withAir(material(tint, alpha));
      }
      if (passes(tint, 1, MIN_CONTRAST)) return withAir(material(tint, 1));
      if (passes(safe, 1, MIN_CONTRAST)) return withAir(material(safe, 1));
      // An opaque tint alone cannot repair arbitrarily faint text. Verify an opaque tint AND ink pair.
      // Raise only failing text's group opacities and move its ink toward the readable endpoint, retaining its hue.
      const opacityNodes = new Set(), repairs = [];
      for (const entry of entries) if (!entryPasses(entry, safe, MIN_CONTRAST)) {
        for (const layer of entry.path) if (layer.node && layer.opacity !== 1) opacityNodes.add(layer.node);
      }
      // Shared opacity groups affect their other text too; verify against the material that will actually be applied.
      const verified = entries.map(entry => {
        const fixed = { ...entry, path: entry.path.map(layer => opacityNodes.has(layer.node) ? { ...layer, opacity: 1 } : layer) };
        if (entryPasses(fixed, safe, MIN_CONTRAST)) return fixed;
        fixed.alpha = 1;
        for (const endpoint of INK_ENDPOINTS) {
          for (let i = 0; i <= SEARCH_STEPS; i++) {
            fixed.rgb = over(entry.rgb, endpoint, i / SEARCH_STEPS).map(Math.round);
            if (!entryPasses(fixed, safe, MIN_CONTRAST)) continue;
            repairs.push(fixed); return fixed;
          }
        }
        // The opaque safe surface has only one local background; an endpoint always reaches AA.
        throw new Error('No readable glass tint-and-ink pair');
      });
      if (!passes(safe, 1, MIN_CONTRAST, verified)) throw new Error('Unverified glass fallback');
      return withAir(material(safe, 1, repairs, opacityNodes), verified);
    }
    function removeRepairs(el) {
      for (const node of [...repaired]) if (!el || el.contains(node)) {
        node.removeAttribute('data-glass-readable'); node.removeAttribute('data-glass-readable-opacity');
        node.style.removeProperty('--glass-readable-ink'); repaired.delete(node);
      }
    }
    function render() {
      frame = 0; if (destroyed || paused) return;
      if (api.prepareInk) api.prepareInk();
      const root = getComputedStyle(html), tint = parse(root.getPropertyValue('--glass-rgb'));
      const safe = parse(root.getPropertyValue('--glass-safe-rgb')), brightness = Number(root.getPropertyValue('--glass-brightness'));
      const saturation = materialSaturation = Number(root.getPropertyValue('--glass-saturation')), blur = root.getPropertyValue('--glass-blur');
      const filmRgb = parse(root.getPropertyValue('--glass-line-rgb'));
      const filmAlpha = Math.max(...FILM_ROLES.map(role => Number(root.getPropertyValue('--glass-film-' + role))));
      const airTints = outdoor ? outdoor.airTints() : [];
      const samples = skySamples(), current = membershipDirty ? new Set(document.querySelectorAll(SURFACES)) : new Set(observed);
      const removed = [...observed].filter(el => !current.has(el)), updates = [];
      // Read geometry, styles and text for affected cards only; weather reuses the cached text descriptions.
      for (const el of current) {
        if (!materialDirty && !allDirty && !dirty.has(el) && text.has(el)) continue;
        if (allDirty || dirty.has(el) || !text.has(el)) {
          text.set(el, readText(el)); solveAfterAnimations(el);
          const r = el.getBoundingClientRect();
          locations.set(el, { x: r.left + r.width / 2 + window.scrollX, y: r.top + r.height / 2 + window.scrollY });
        }
        const material = solve(samples, tint, text.get(el), brightness, saturation, safe, filmRgb, filmAlpha, airTints, outdoor ? outdoor.airAlphaMax() : 0);
        const backdrop = material.alpha < 1 ? frost(samples[0] || tint, brightness, saturation) : tint;
        const apparent = over(backdrop, material.rgb, material.alpha).map(Math.round);
        updates.push({ el, material, apparent });
      }
      // Every read above is complete before the first DOM write below.
      // Repair selectors are theme-scoped, so the reads above already see the new theme's original ink.
      if (resetInk) { removeRepairs(); resetInk = false; }
      for (const el of removed) {
        observer.unobserve(el); observed.delete(el); text.delete(el); materials.delete(el); locations.delete(el); liveAlpha.delete(el); removeRepairs(el);
        for (const key of MATERIAL_PROPERTIES) el.style.removeProperty(key);
      }
      for (const { el, material, apparent } of updates) {
        if (!observed.has(el)) { observer.observe(el); observed.add(el); }
        materials.set(el, material);
        const properties = { '--glass-tint': material.rgb.join(' '), '--glass-alpha': String(material.alpha),
          '--glass-surface': `rgb(${apparent.join(' ')})`,
          '--glass-backdrop': material.alpha < 1 ? `blur(${blur}) saturate(${saturation}) brightness(${brightness})` : 'none' };
        for (const [key, value] of Object.entries(properties)) if (el.style.getPropertyValue(key) !== value) el.style.setProperty(key, value);
        for (const entry of material.repairs) {
          entry.node.setAttribute('data-glass-readable', html.dataset.theme);
          entry.node.style.setProperty('--glass-readable-ink', `rgb(${entry.rgb.join(' ')})`); repaired.add(entry.node);
        }
        for (const node of material.opacityNodes) { node.setAttribute('data-glass-readable-opacity', html.dataset.theme); repaired.add(node); }
      }
      allDirty = false; materialDirty = false; membershipDirty = false; dirty.clear();
      applyAirMaterial();
      if (updates.length) api.invalidateWetMaterial?.();
    }
    // A card's colour now: its everyday glass, with its verified share of what the pass lends (none once settled).
    const lent = material => outdoor && material.airShare ? over(material.rgb, outdoor.materialColor(material.rgb), material.airShare) : material.rgb;
    // The sky line's chips and the Alerts bell are opaque, not glass: in the pass they take the same air colour over the
    // theme's glass (html.sky-air-cast, --glass-air-rgb, --glass-air-edge in sky-air.css), so none stays white among tinted cards.
    let chipBase = null, veilMatrix = null;
    function veilFilter(rgb) {
      if (!veilMatrix) {
        if (typeof document.createElementNS !== 'function' || !document.body) return;
        const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'), filter = document.createElementNS(ns, 'filter');
        svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('width', '0'); svg.setAttribute('height', '0');
        svg.style.position = 'absolute'; svg.style.pointerEvents = 'none';
        filter.setAttribute('id', 'sky-air-veil'); filter.setAttribute('color-interpolation-filters', 'sRGB');
        veilMatrix = document.createElementNS(ns, 'feColorMatrix'); veilMatrix.setAttribute('type', 'matrix');
        filter.appendChild(veilMatrix); svg.appendChild(filter); document.body.appendChild(svg);
      }
      const [r, g, b] = rgb.map(v => Math.round(v / 255 * 1000) / 1000);
      const values = `${r} 0 0 0 0 0 ${g} 0 0 0 0 0 ${b} 0 0 0 0 0 1 0`;
      if (veilMatrix.getAttribute('values') !== values) veilMatrix.setAttribute('values', values);
    }
    function castChips(cast) {
      const root = document.documentElement, on = cast > .001 && !!outdoor && !!outdoor.airTint();
      if (!on) {
        if (root.classList.contains('sky-air-cast')) {
          root.classList.remove('sky-air-cast');
          for (const key of ['--glass-air-rgb', '--glass-air-edge']) root.style.removeProperty(key);
        }
        return;
      }
      const theme = api.dark && api.dark() ? 1 : 0;
      if (!chipBase || chipBase.theme !== theme) chipBase = { theme, rgb: parse(getComputedStyle(root).getPropertyValue('--glass-rgb') || '255 255 255') || [255, 255, 255] };
      const tint = outdoor.materialColor(chipBase.rgb.slice(0, 3)).map(Math.round), edge = over([255, 255, 255], tint, cast * EDGE_AIR_SHARE).map(Math.round);
      root.style.setProperty('--glass-air-rgb', tint.join(' '));
      root.style.setProperty('--glass-air-edge', `rgb(${edge.join(' ')} / ${EDGE_ALPHA[theme]})`);
      // The drawings (monitor scenes and devices) are multiplied by the air's colour through an SVG filter (#sky-air-veil):
      // whites turn the glass's tan, lines stay dark, and nothing around them changes. A dark theme's tint is scaled up
      // first, so the drawings warm without blackening.
      const air = outdoor.airTint(), lift = theme ? VEIL_DARK_MAX / Math.max(1, ...air) : 1;
      const veil = over([255, 255, 255], air.map(v => Math.min(255, v * lift)), cast);
      veilFilter(veil);
      root.classList.add('sky-air-cast');
    }
    // Cached, verified tint/alpha pairs only: animation never scans text or runs the contrast solver.
    function applyAirMaterial() {
      const sky = api.sky(), { H } = api.size(), cast = outdoor ? outdoor.glassCast() : 0;
      for (const [el, material] of materials) {
        const rgb = lent(material), alpha = material.alpha + (material.airAlpha - material.alpha) * cast;
        const location = locations.get(el), x = location.x - window.scrollX, y = location.y - window.scrollY;
        const sample = over(sky.top, sky.hor, clamp(y / H));
        const bg = outdoor ? outdoor.sampleBackground(sample, x, y, 'rear') : sample;
        const apparent = over(frost(bg, material.backdropBrightness, materialSaturation), rgb, alpha).map(Math.round);
        liveAlpha.set(el, alpha);
        el.style.setProperty('--glass-alpha', String(Math.round(alpha * 1000) / 1000));
        el.style.setProperty('--glass-tint', rgb.map(Math.round).join(' '));
        el.style.setProperty('--glass-surface', `rgb(${apparent.join(' ')})`);
        if (cast > .001) {
          const edge = over([255, 255, 255], rgb, cast * EDGE_AIR_SHARE).map(Math.round);
          const theme = api.dark && api.dark() ? 1 : 0;
          el.style.setProperty('--glass-edge', `rgb(${edge.join(' ')} / ${EDGE_ALPHA[theme]})`);
          el.style.setProperty('--glass-sheen', `rgb(${edge.join(' ')} / ${SHEEN_ALPHA[theme]})`);
        } else if (el.style.getPropertyValue('--glass-edge')) { el.style.removeProperty('--glass-edge'); el.style.removeProperty('--glass-sheen'); }
      }
      castChips(cast);
    }
    function schedule() { if (!destroyed && !paused && !frame) frame = requestAnimationFrame(render); }
    function redraw() { materialDirty = true; if (outdoor) outdoor.redraw(); schedule(); }
    function invalidate() { allDirty = true; membershipDirty = true; redraw(); }
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) dirty.add(entry.target);
      if (outdoor) outdoor.invalidateGeometry(); schedule();
    });
    const contents = new MutationObserver(records => {
      let relevant = false;
      for (const record of records) {
        const node = record.target.nodeType === 1 ? record.target : record.target.parentElement;
        if (!node || node.closest(TRANSIENT)) continue;
        const el = node.closest(SURFACES);
        if (el) dirty.add(el);
        if (record.type === 'childList' || record.type === 'attributes') membershipDirty = true;
        relevant = true;
      }
      if (relevant) { if (outdoor) outdoor.invalidateGeometry(); schedule(); }
    });
    contents.observe(document.querySelector('.page') || document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'hidden'] });
    const theme = new MutationObserver(() => { resetInk = true; invalidate(); });
    theme.observe(html, { attributes: true, attributeFilter: ['data-theme'] });
    document.fonts.ready.then(invalidate);
    outdoor = createOutdoor(api, applyAirMaterial);
    redraw();
    return {
      setConditions(conditions, primary) { outdoor.setConditions(conditions, primary); materialDirty = true; schedule(); },
      play: kind => outdoor.play(kind), settle: () => outdoor.settle(), still: () => outdoor.still(),
      stop: () => outdoor.stop(), burst: kinds => outdoor.burst(kinds),
      clear() { outdoor.clear(); materialDirty = true; schedule(); },
      resize() { outdoor.resize(); invalidate(); }, redraw,
      sampleBackground: (rgb, x, y) => outdoor.sampleBackground(rgb, x, y),
      sampleRear: (rgb, x, y) => outdoor.sampleBackground(rgb, x, y, 'rear'),
      sampleFront: (rgb, x, y) => outdoor.sampleBackground(rgb, x, y, 'front'),
      get smokeCover() { return outdoor.smokeCover; },
      get frontActive() { return outdoor.frontActive; },
      glassFor(element) {
        const el = element.closest(SURFACES), material = materials.get(el);
        if (material) {
          return { rgb: lent(material), alpha: liveAlpha.get(el) ?? material.alpha, backdropBrightness: material.backdropBrightness };
        }
        const solid = element.closest('.card, .sum-card') || element, cs = getComputedStyle(solid);
        const rgb = parse(cs.backgroundColor) || parse(cs.getPropertyValue('--card'));
        return { rgb: rgb.slice(0, 3), alpha: rgb[3] ?? 1, backdropBrightness: 1 };
      },
      setPaused(hidden) { paused = hidden; outdoor.setPaused(hidden); if (paused) { cancelAnimationFrame(frame); frame = 0; } else redraw(); },
      destroy() {
        destroyed = true; outdoor.destroy(); cancelAnimationFrame(frame); observer.disconnect(); contents.disconnect(); theme.disconnect(); removeRepairs();
        for (const el of materials.keys()) for (const key of MATERIAL_PROPERTIES) el.style.removeProperty(key);
        materials.clear(); locations.clear(); text.clear(); observed.clear(); html.classList.remove('sky-glass');
      },
    };
  }
  return { create, exhaustPalette, roadBearing, plumeFrame, exhaustStrength };
})();
