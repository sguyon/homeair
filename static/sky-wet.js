// Condensation on the existing glass. No data rules, controller, replay timer or work before create(api).
var SkyWet = (() => {
  const SOFT_BUFFER_SCALE = { phone: .5, wide: .25 }; // canvas px/CSS px; fog, wipe and blurred scene buffers: half size on a phone, so a wipe's edge and a drop's 5 px trail stay crisp; quarter size wider.
  const PHONE_WIDTH_PX = 700; // CSS px; viewports narrower than this use the phone's buffer scale.
  const CRISP_BUFFER_SCALE = 1; // canvas px/CSS px; wider screens draw beads at CSS resolution; a phone (under PHONE_WIDTH_PX) at its own density, so its small beads keep their shaded rims.
  const VIEWPORT_PIXEL_MAX = 1600000; // pixels/target; bound the single viewport GL target on large displays.
  const SOFT_PIXEL_MAX = 100000; // pixels/soft target; keep fog, wipe and scene copies bounded together on large displays.
  const CARD_PIXEL_MAX = 1600000; // pixels/all visible card copies; overlapping cards share one allocation budget.
  const BUFFER_DIMENSION_MAX = 4096; // px/axis; conservative Canvas fallback limit, further restricted by GL limits.
  // The pass follows the picked round 2 glass damp (C): the whole screen fogs like breath on cold glass, over the words for
  // about a second, then the breath lifts from the edges in, the mist behind the words thins from the edges to the settled
  // film, settled at about 3.8 s after play. Unlike C, the fog never pops to a white frame: it blooms in, soft and milky.
  // Then the condensation leaves the cards: their beads, film and running drops stay until the drops have run, then fade, and the
  // cards are their everyday frosted glass. The mist and beads stay only behind them, on the sky between and around the cards.
  const CARD_DRY_AT_MS = 10000; // ms after play; the drops start by 3.7 s and most have stopped at a word or left the screen by now (about 10.6 s after load).
  const CARD_DRY_MS = 2000; // ms; the cards' condensation evaporates this slowly, never a cut (a skip takes SKIP_FADE_MS).
  const SKIP_FADE_MS = 250; // ms; skipping eases only the remaining condensation into quiet mist.
  const FOG_GROW_MS = 650; // ms; the breath blooms up from below the screen, its front a wide soft gradient, never a white frame.
  const FOG_THIN_AT_MS = 2300; // ms after play; the full fog holds until the wipe ends, so the hand's swath reads as a wipe.
  const FOG_THIN_MS = 1500; // ms; the mist thins from the edges in to the settled film (settled by about 3.8 s after play).
  const BLOOM = { y: 1.08, soft: .55, fade: .85 }; // the breath's source (viewport heights down, below the bottom centre), its front's width (share of its reach), and the share of FOG_GROW_MS its opacity takes to come in.
  const CLEAR_SOFT = .38; // share of the half-viewport; the clearing front is soft, a fog thinning from the frame, not a hard ring.
  const CLEAR_LUMPS = { amplitude: .12, cell: 110 }; // share of the depth and CSS px: smooth random lumps (value noise) make the clearing edge wander like real evaporation, never stripes.
  // In front of the words: the breath itself (C's front fog), milky, with the fine condensation grain.
  const FRONT_PEAK = { light: .8, dark: .66 }; // opacity at full fog; the words ghost through, as on C, without a paper-white frame.
  const FRONT_CLEAR_AT_MS = 1000; // ms after play; the breath holds over the words until the hand is about to cross it.
  const FRONT_CLEAR_MS = 850; // ms; the breath lifts from the edges in: over the words for about a second, readable again by about 1.85 s after play.
  const FRONT_RGB = { light: [242, 246, 250], dark: [148, 160, 176] }; // the breath's milk (C's front fog colours).
  const FRONT_GRAIN = { tile: 128, count: 900, alpha: .8, radius: [.3, 1.2, 2.2] }; // CSS px/tile, droplets/tile, opacity, radius base/span/bias: C's fine condensation droplets.
  const FRONT_PIXEL_MAX = 1400000; // pixels; the breath's canvas is at the screen's own density on a phone (its droplets are sub-pixel fine), bounded on large displays.
  const FRONT_GRAIN_INK = { light: [[40, 60, 80, .2], [255, 255, 255, .95]], dark: [[0, 0, 0, .3], [225, 235, 248, .22]] }; // rgba of each droplet's shadow and its lit body.
  const WIPE_AT_MS = 1100; // ms after play; the pane has been fully fogged a while before the hand arrives (the prototype's 1.5 s on screen).
  const WIPE_DURATION_MS = 1400; // ms; one unhurried palm pass across the Summary, easy to follow (the prototype's 1.5 to 3.0 s).
  const WIPE_INSET_PX = 16; // CSS px; the palm lands and lifts inside the card, not in the gutter beside it.
  const WIPE_HAND_HOLD_MS = 300; // ms; the hand's swath stays clear briefly behind the palm before misting back.
  const BEAD_AT_MS = [250, 300]; // ms after play; the pass's beads start forming as the fog thickens.
  const BEAD_GROW_MS = [500, 1800]; // ms; each bead swells to its size over a time in this range (all formed by about 2 s).
  // Three drops run down the pane once the breath has lifted (C's runners), stop and go, leaving clear trails; they keep running
  // into the settled state until they leave the screen.
  const RUN_AT_MS = [1900, 2500, 3100, 3700]; // ms after play; when each drop starts to run (one per entry), from C's 1.5 s on. One more than C's three: words stop some drops early, so two or three long trails remain.
  const RUN_GROW_MS = 1400; // ms; a runner beads up for this long before it runs.
  const RUN_RADIUS_PX = [3.6, 5]; // CSS px; a runner is one of the larger drops.
  const RUN_START = { left: .05, width: .9, top: .04, height: .4 }; // viewport fractions; where runners may start: the upper part of the pane, inside the screen's edge gutter.
  const RUN_SPEED = { max: 170, accel: .4, brake: .65 }; // CSS px/s top speed, px/s gained per ms while sliding, speed kept per frame while caught.
  const RUN_CATCH = { period: 240, threshold: -.4 }; // ms per radian and sine level: the drop slides, catches on the glass, slides again.
  const RUN_WANDER = [4, 31, 1.6, 11]; // CSS px amplitude and px/radian, twice: its path wavers as C's, never a ruler line.
  // A drop never runs through a word: it sees one coming and curves beside it, at most RUN_DODGE.max px aside; with no way
  // past, it stops above the word and stays as a bead.
  const RUN_DODGE = { ahead: 40, max: 44, gap: 1, probe: 2, pull: .07, damp: .72, catchUp: 1.5 }; // CSS px look-ahead, widest detour, clearance from a glyph, search step; and the
  // sideways spring per trail step (pull toward the detour, velocity kept): the path bends round a word in a smooth curve, never a step; when a word
  // comes up too close for the spring, the drop slides aside at most catchUp px per px of its step before going on.
  const RUN_COLUMNS = { tries: 48, apart: 56 }; // candidate starts across the whole pane, and the CSS px kept between chosen columns: each drop starts
  // where its run down is longest before a word (on a phone often a card's margin), so two or three trails reach far.
  const RUN_SHAPE = { stretch: .3, tail: [2.2, 1.4, 1.6, 1, .55] }; // body stretch at full speed; tail offset, offset per speed, length, length per speed, half-width (radii).
  const RUN_TRAIL = { rx: .62, ry: 1.1, behind: 1.2, step: 2 }; // trail stamp size and its distance above the head (radii), CSS px between stamps.
  const RUN_STEPS_MAX = 400; // trail steps per drop per frame at most: a drop sliding aside never stalls a frame.
  const RUN_FRAME_MAX_MS = 50; // ms; a long frame (a busy main thread) moves a runner at most this much, never a jump.
  const RUN_TRAIL_HOLD_MS = 9000; // ms; a runner's trail stays clear this long before misting back (C's stayed for good).
  const INK_PASS_MS = 100; // ms; during a pass the sky's ink follows the fog at 10 Hz (each measure reads the sky back), not every frame.
  const BEAD_GROW_STEP_MS = 50; // ms; growing beads are re-rasterized at most this often (each raster reads back the bead map).
  const WIPE_RADIUS_X = 22; // CSS px; overlapping stamps give a continuous palm stroke.
  const WIPE_RADIUS_Y = 37; // CSS px; a 74 px palm clears the actual humidity action.
  const WIPE_ARC_PX = 12; // CSS px; a slight forearm arc avoids a ruler-straight painted bar.
  const WIPE_TILT_PX = 8; // CSS px; the hand falls slightly as it crosses the pane.
  const WIPE_STEP_PX = 3; // CSS px/stamp; keep the clear swath continuous at low frame rates.
  const WIPE_EDGE_PX = 1.5; // CSS px; a drop's trail has a crisp edge (a soft edge on a 5 px trail would barely clear it).
  const WIPE_HOLD_MS = 1400; // ms; retain the cleared glass before misting back.
  const HAND_REGROW_MS = 900; // ms; the automatic palm's swath mists back fast, into the settled film (the pass resolves by about 3.8 s).
  const REGROW_MS = 2600; // ms; finite regrowth sleeps once the pane has misted back.
  const FINGER_WIDTH = { touch: [56, 72], mouse: [64, 80] }; // CSS px/diameter; larger, slow strokes spread across the picked finger-width range.
  const FINGER_FAST_SPEED = 1.2; // CSS px/ms; strokes at this speed use the narrow end of the finger range.
  const FINGER_MIN_DT_MS = 8; // ms/sample; avoid unstable widths for coalesced or synthetic pointer events.
  const FINGER_THRESHOLD = { touch: 8, mouse: 4 }; // CSS px; distinguish a tap from a deliberate sideways wipe.
  const FINGER_HORIZONTAL_RATIO = 1.1; // horizontal/vertical travel; preserve scrolling for ambiguous diagonal drags.
  const CONTROL_SELECTOR = 'a, button, input, select, textarea, label, summary, [role="button"], [role="link"], [role="slider"], [contenteditable]:not([contenteditable="false"]), [tabindex], svg.ts, [data-chart-scrub]';
  const EDGE_BEAD_GAP_PX = 1.5; // CSS px; keep beads pushed by the palm just beyond its feathered edge.
  const PALM_WAVE_PX = [19, 53]; // CSS px/radian, twice; vary the palm's height along its path, not only around each stamp.
  const PALM_WIDTH_VARIATION = .12; // fraction of palm radius; the swath's height wanders along it like a hand's pressure, never an even bar.
  const PALM_TAPER = { least: .4, land: .18, lift: .3 }; // the palm's height at its ends (share of full) and the shares of its path it takes to press in and to lift off.
  const LAYOUT_MOVE_PX = 1; // CSS px; the action or its card moving more than this clears the swath (sub-pixel reflows do not).
  const WIPE_PAST_TEXT_PX = 36; // CSS px; the palm lifts this far past the action's words, not at the card's far edge.
  const WIPE_EDGE_SOFT_PX = 6; // CSS px; a palm or finger swath fades into the fog over this width: a soft fog edge, never an outline.
  const SETTLED_FILM = { light: .42, dark: .25 }; // fraction of full fog; a milky film stays over the whole window (about the prototype's .34 light, .16 dark veil once the sky's fog opacity applies).
  const SETTLED_CARD_FILM = .52; // fraction of full fog; a wipeable veil covers card faces, including above the cold lower edge. No trace: the settled state is only this mist.
  const SETTLED_GRAIN = .6; // fraction of peak microdroplet light; settled mist keeps its fine texture in both backends.
  const SETTLED_BOTTOM = .28; // share of viewport height; cold glass is mistiest low on the pane.
  const SETTLED_EDGE_PX = 78; // CSS px; each visible card retains a cool, damp lower edge.
  const SETTLED_EDGE_FOG = .72; // fraction of full fog; retain wet glass below the card's words.
  const BEAD_GROUPS = [[5000, 3, 6.4], [500, .9, 3.5]]; // CSS px²/bead and radius px: a little denser than C's one in 620 px² (words keep theirs clear), a tenth of them large.
  const BEAD_MAX = 2200; // beads/viewport; bound texture painting and memory on a wide screen (C's density up to about 1 Mpx).
  const GLYPH_CELL_PX = 4; // CSS px; the grid that marks where words are, so no bead sits under a glyph.
  const GLYPH_HEIGHT = 1.05; // font sizes; a line's glyphs span about this much of its line box, centred (the leading above and below keeps its beads).
  const GLYPH_MARGIN_PX = 1; // CSS px; a bead keeps this clear of a glyph's box.
  const BEAD_RADIUS_BIAS = 2; // unitless exponent; favor the smaller lenses in each size group.
  const LENS_RADIUS_PX = 16; // CSS px; radius encoded in the normal texture's blue channel.
  const SPRITE_SCALE = 2; // sprite px/CSS px; cached subpixel normals keep small beads rounded.
  const SPRITE_RADIUS_STEP = .25; // CSS px; quantized sprite cache avoids a texture for each bead.
  const SPRITE_MIN_PX = 4; // sprite px/axis; four samples preserve a round normal even for subpixel beads.
  const BURST_DROP_MAX = 2; // drops/event; the controller owns the shared sparse-particle budget.
  const BURST_MS = 1400; // ms; one finite, faint settled event, never a replay.
  const BURST_TRAVEL_PX = 40; // CSS px/event; a short quiet drop run behind text.
  const BEAD_SETTLED_ALPHA = .8; // opacity multiplier; settled beads stay visible (C's), a little quieter than the pass. None sits under a word.
  const LENS = { refract: 2.2, curve: 6, clear: .94, cardClear: .3, body: 1, cardBody: .14, fogMix: .55, rim: .55, cardRim: 1, highlight: .6, foot: .1 }; // unitless lens optics; shared by GPU rendering and ink samples. A card lens clears little of the film: a shallow lens, not a bright disc (white discs read as snow). A sky lens's centre takes fogMix of the fogged pane: a clear drop, not a dark speck of raw sky.
  const BEAD_LIT = { light: .55, dark: .3, edge: [.72, 1] }; // opacity per theme and normalized radii of its soft edge: a bead's lit body (C's), so it reads on a thin settled film where refraction alone barely shows.
  const LENS_RIM = [.42, .78, -.35, .25]; // normalized radius/Y thresholds; a shaded crescent on the lower half (C's beads), wide enough to show on a 1 px bead.
  const LENS_HIGHLIGHT = [.14, .28, -.3, -.36, .7, 2.5]; // normalized spot radii/centre and CSS px radius bounds; tiny beads cannot become white discs.
  const LENS_FOOT = [.4, .8, .7, .92, .15, .45]; // normalized Y/radius thresholds; a faint bright foot below the clear body.
  // A wiped swath is glass cleared: on a card it shows the card at its own clean colour (the sky behind a card keeps its fog, so the
  // card's frosted backdrop does not darken), and its border is a crisp wet ridge of the light colour, at this opacity.
  // A wiped swath is glass cleared: inside it the card shows exactly as it does with no fog. The settled film on a card takes the card's
  // own colour (as seen through the sky's fog behind it), so it shows only as its grain and beads, and taking it away changes no colour.
  const PUSH_MIN_R = 2; // CSS px; the larger beads are pushed to a swath's rim, where they show; the smaller ones are wiped away.
  // A running drop's trail on a card is clearer glass: the room beyond shows a little darker (C's), never where a word is.
  const TRAIL_GLASS = .5; // share of the card's glass opacity left in a drop's trail: a thin darker clear line, as on C.
  const TRAIL_SHADE = { light: [1, 0], dark: [.45, .55] }; // per theme: what a trail keeps of its colour on a card, and how far it darkens the open sky. Dark glass needs the
  // darker line C has there; light glass already reads clearer (the film it cuts is milky).
  const SETTLED_HAZE = .2; // share of the fog's milk in the settled film on a card: a light haze the trails and a wipe cut through (shows in dark; light cards are near the milk already).
  const LENS_WIPE = [.03, .3]; // cleared fractions; remove lenses inside the palm's clear swath.
  const FOG_PATTERN = { body: .86, wave: .08, lower: .06, x: .044, y: .036 }; // opacity and radians/map px; smooth milky grain, not dark gravel.
  const FOG_GRAIN = { tile: 128, count: 520, radius: .65, lift: .22 }; // CSS px/tile, flecks/tile, radius CSS px and light mix; lit microdroplets distinguish peak mist from streaking rain.
  const PALM_EDGE = { wave: .065, fine: .035, lobes: 5, fineLobes: 11, phase: 1.3 }; // unitless; keep the palm's outline irregular without breaking the swath.
  const BURST_START = { left: .1, width: .8, top: .2, height: .6, radius: 2 }; // viewport fractions and CSS px; occasional drops stay away from hard viewport edges.
  const CARD_BEADS = { area: 300, least: 40 }; // CSS px² per bead on a card, and the fewest a card gets: phones keep C's dense field on the cards.
  const PUSHED_GROW = 1.25; // a pushed bead gathers the water it was pushed with: this much larger at the swath's rim, so it shows.
  const CARD_SELECTOR = '.sum-card, .card:not(.off, .mc-ghost, .hist-off, .no-token-card), .bar .custom-top, .now-line .dsp-top';
  const TRANSIENT = '.sx-tip, .sx-hv, [data-chart-scrub], .sky-wet-pane';
  // Only canvases below wet: rain, flashes and front outdoor matter are drawn separately above it.
  const BACKDROP_CANVASES = '#sky-stars, #sky-clouds, #sky-wind, .sky-air-back-soft, .sky-air-back-fine';
  const MATERIAL_LUMA = [.213, .715, .072]; // RGB luminance weights; match the shader when sampling the shared glass material.
  const TAU = Math.PI * 2;
  const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));
  const smooth = x => { x = clamp(x); return x * x * (3 - 2 * x); };
  const ease = x => 1 - (1 - clamp(x)) ** 3;
  const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
  const parse = s => (s.trim().match(/-?[\d.]+/g) || []).slice(0, 3).map(Number);
  const colour = (c, a = 1) => `rgba(${c.join(',')},${clamp(a)})`;
  const canvas = () => document.createElement('canvas');
  function paneDistance({ r, round }, x, y) {
    const qx = Math.abs(x - r.left - r.width / 2) - r.width / 2 + round;
    const qy = Math.abs(y - r.top - r.height / 2) - r.height / 2 + round;
    return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - round;
  }

  const VERTEX = `attribute vec2 p; varying vec2 uv;
    void main() { uv=vec2(p.x*.5+.5,.5-p.y*.5); gl_Position=vec4(p,0.,1.); }`;
  const FRAGMENT = `precision mediump float;
    varying vec2 uv;
    uniform sampler2D fogMap, beads, view, grainMap;
    uniform vec2 px;
    uniform vec4 bounds;
    uniform vec3 milk, shift, shade, light, glass;
    uniform float fogAlpha, glassAlpha, brightness, saturation, card, beadAlpha, amount, cardMilk, beadLit, skyFog, restFog;
    uniform vec2 trailShade;
    vec4 over(vec4 a, vec3 c, float b) { return vec4(c*b+a.rgb*(1.-b),b+a.a*(1.-b)); }
    vec3 material(vec3 c, float a) {
      float g=dot(c,vec3(.213,.715,.072));
      return mix(clamp((vec3(g)+(c-vec3(g))*saturation)*brightness,0.,1.),glass,a);
    }
    // The card as it looks with no fog of its own: its glass over the sky and the sky's fog behind it (blue: the sky's wipe).
    vec3 pane(vec2 at) {
      at=clamp(at,0.,1.); vec4 f=texture2D(fogMap,at);
      return material(mix(texture2D(view,at).rgb,milk,skyFog*f.r*(1.-f.b)),glassAlpha);
    }
    void main() {
      vec4 f=texture2D(fogMap,uv), b=texture2D(beads,uv);
      // Card-bottom mist exists only in a card draw, never in the viewport map.
      vec2 point=uv/px;
      float local=${SETTLED_EDGE_FOG.toFixed(2)}*smoothstep(0.,${SETTLED_EDGE_PX.toFixed(1)},point.y-bounds.w+${SETTLED_EDGE_PX.toFixed(1)});
      local=max(local,${SETTLED_CARD_FILM.toFixed(2)});
      float density=max(f.r,card*local*(1.-amount));
      // A card's wipe is green; the open sky's is blue, which is never wiped under a card.
      float wiped=mix(f.b,f.g,card), amt=density*(1.-wiped);
      vec3 own=pane(uv), fogColour=mix(milk,mix(mix(own,milk,${SETTLED_HAZE.toFixed(2)}),mix(clamp(own+shift,0.,1.),milk,cardMilk),amount),card);
      // In a card's swath during the pass, the card as it looks with no fog: its glass over the settled sky, not over the pass's
      // thicker fog behind it (which would show the swath lighter in light theme).
      vec3 bare=material(mix(texture2D(view,uv).rgb,milk,skyFog*restFog),glassAlpha);
      float excess=clamp((f.r-restFog)/max(1.-restFog,.01),0.,1.)*amount;
      float grain=texture2D(grainMap,fract(point/${FOG_GRAIN.tile.toFixed(1)})).a*mix(${SETTLED_GRAIN.toFixed(2)},1.,amount)*${FOG_GRAIN.lift.toFixed(2)};
      float opacity=fogAlpha*amt;
      vec4 outC=vec4(fogColour*opacity,opacity);
      outC=over(outC,light,grain*amt);
      outC=over(outC,bare,card*wiped*excess);
      // A drop's trail on a card (alpha: 1 - its share): clearer glass, the room beyond a little darker.
      outC=over(outC,material(texture2D(view,uv).rgb,glassAlpha*${TRAIL_GLASS.toFixed(2)})*trailShade.x,card*(1.-f.a));
      outC=over(outC,vec3(0.),(1.-card)*(1.-f.a)*trailShade.y);
      vec2 n=(b.rg-.5)*2.; float rr=length(n), r=b.b*${LENS_RADIUS_PX.toFixed(1)};
      float dm=b.a*beadAlpha*(1.-smoothstep(${LENS_WIPE.join(',')},wiped));
      vec2 inverted=uv-n*px*r*(${LENS.refract.toFixed(2)}+rr*rr*${LENS.curve.toFixed(2)});
      float rim=smoothstep(${LENS_RIM.slice(0, 2).join(',')},rr)*smoothstep(${LENS_RIM.slice(2).join(',')},n.y);
      float hl=(1.-smoothstep(${LENS_HIGHLIGHT.slice(0, 2).join(',')},length(n-vec2(${LENS_HIGHLIGHT.slice(2, 4).join(',')}))))*smoothstep(${LENS_HIGHLIGHT.slice(4).join(',')},r);
      float foot=smoothstep(${LENS_FOOT.slice(0, 2).join(',')},n.y)*(1.-smoothstep(${LENS_FOOT.slice(2, 4).join(',')},rr))*smoothstep(${LENS_FOOT.slice(4).join(',')},rr);
      // A lens clears the fog. Its body samples the actual outdoor pane upside down.
      // Card lenses reveal the same current outdoor pane through its material, without any milk.
      vec3 seen=texture2D(view,uv).rgb*(1.-outC.a)+outC.rgb;
      vec3 skyLens=mix(texture2D(view,clamp(inverted,0.,1.)).rgb,seen,${LENS.fogMix.toFixed(2)}*(1.-rr));
      outC*=1.-dm*mix(${LENS.clear.toFixed(2)},${LENS.cardClear.toFixed(2)},card);
      outC=over(outC,mix(skyLens,pane(inverted),card),dm*mix(${LENS.body.toFixed(2)},${LENS.cardBody.toFixed(2)},card));
      outC=over(outC,light,dm*beadLit*(1.-smoothstep(${BEAD_LIT.edge.map(v => v.toFixed(2)).join(',')},rr)));
      outC=over(outC,shade,dm*rim*mix(${LENS.rim.toFixed(2)},${LENS.cardRim.toFixed(2)},card));
      outC=over(outC,light,dm*(hl*${LENS.highlight.toFixed(2)}+foot*${LENS.foot.toFixed(2)}));
      gl_FragColor=outC;
    }`;

  // Initialization is transactional: a failed shader/link leaves no context resources or partly installed layer.
  function gpu(target) {
    let gl, program, buffer;
    const shaders = [], textures = [];
    function dispose() {
      if (!gl) return;
      gl.useProgram(null);
      for (const t of textures) gl.deleteTexture(t);
      for (const s of shaders) gl.deleteShader(s);
      if (buffer) gl.deleteBuffer(buffer);
      if (program) gl.deleteProgram(program);
      program = buffer = null;
    }
    try {
      gl = target.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false });
      if (!gl) return null;
      program = gl.createProgram();
      for (const [kind, source] of [[gl.VERTEX_SHADER, VERTEX], [gl.FRAGMENT_SHADER, FRAGMENT]]) {
        const shader = gl.createShader(kind); shaders.push(shader);
        gl.shaderSource(shader, source); gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error('Wet glass shader unavailable');
        gl.attachShader(program, shader);
      }
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Wet glass program unavailable');
      gl.useProgram(program);
      buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      const p = gl.getAttribLocation(program, 'p'); gl.enableVertexAttribArray(p); gl.vertexAttribPointer(p, 2, gl.FLOAT, false, 0, 0);
      const maps = ['fogMap', 'beads', 'view', 'grainMap'];
      const U = Object.fromEntries([...maps, 'px', 'bounds', 'milk', 'shift', 'shade', 'light', 'glass', 'fogAlpha', 'glassAlpha', 'brightness', 'saturation', 'card', 'beadAlpha', 'amount', 'cardMilk', 'beadLit', 'skyFog', 'restFog', 'trailShade'].map(k => [k, gl.getUniformLocation(program, k)]));
      for (const [i, key] of maps.entries()) {
        const t = gl.createTexture(); textures.push(t); gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.uniform1i(U[key], i);
      }
      const dimensions = [];
      const viewportLimit = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
      const dimension = Math.min(BUFFER_DIMENSION_MAX, gl.getParameter(gl.MAX_TEXTURE_SIZE), gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), ...viewportLimit);
      return { gl, U, dispose, dimension,
        upload(i, src, data, rows) {
          gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, textures[i]);
          const same = dimensions[i]?.[0] === src.width && dimensions[i]?.[1] === src.height;
          // Reuse allocations; a held/regrowing wipe only uploads its changed band of rows.
          if (data) {
            if (same && rows) gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, rows[0], src.width, rows[1] - rows[0] + 1, gl.RGBA, gl.UNSIGNED_BYTE,
              data.subarray(rows[0] * src.width * 4, (rows[1] + 1) * src.width * 4));
            else if (same) gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, src.width, src.height, gl.RGBA, gl.UNSIGNED_BYTE, data);
            else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, src.width, src.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
          } else if (same) gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, src);
          else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
          dimensions[i] = [src.width, src.height];
        },
      };
    } catch (_) { dispose(); return null; }
  }

  // Optional factory-only backend selection for device checks; no URL flag or persistent preference.
  function create(api, { force2D = false } = {}) {
    const html = document.documentElement, layers = new Map(), sprites = new Map(), observed = new Set();
    let output, backend, fogC, viewC, beadC, fogX, viewX, beadX, grainImage;
    const grainPoints = [], lensTiles = new Map(), contacts = new Set();
    let pointerState = null;
    let W = 1, H = 1, scale = 1, softScale = SOFT_BUFFER_SCALE.wide, fw = 1, fh = 1, fogImage, fallbackImage, beadImage, viewImage, cleared, holdUntil, regrowFor, base;
    // The breath in front of the words (above the page, below the header) and the per-texel shape of its bloom and clearing.
    let frontC = null, frontX, frontMaskC, frontMaskX, frontImage, frontGrain = null, frontGrainKey = '', frontShown = false, frontScale = 1, bloomD, edgeE;
    let active = false, paused = document.hidden, destroyed = false, frame = 0, wake = 0, mode = 'settled', passAt = 0;
    let amount = 0, stopAt = 0, stopFrom = 0, frozen = false, lastHand = null;
    // How much condensation is on the cards (1 from a pass until it dries, 0 settled: then no card has a pane and nothing is wiped).
    let cardWet = 0, dryAt = Infinity, dryFrom = 0, dryMs = CARD_DRY_MS;
    let layoutDirty = true, sceneDirty = true, beadsDirty = true, fogDirty = true, tokensDirty = true, resizeDue = false, tokens, cards = [], action = null;
    let inkPending = false, inkAt = 0, inkMode = 'settled', settledFilm = SETTLED_FILM.light, drops = [], runners = [], lastTick = 0, burstDrops = [], burstAt = 0, fogBusy = false, sceneSources = [], sourcesDirty = true, materialDirty = true;
    let nextRegrowAt = Infinity, regrowing = false, outputDirty = true, rasterDrops = [], forming = false, formedAt = 0;
    // Texels under a card (its wipe never clears the sky behind it), and the cells where words are (no bead sits under one).
    let trailMark = new Uint8Array(1), underCard = new Uint8Array(1), glyphs = new Uint8Array(1), glyphW = 1, glyphH = 1, glyphsDirty = true;
    const reduced = () => typeof api.reduce === 'function' ? api.reduce() : !!api.reduce;
    const invalidate = () => { if (api.invalidateInk) api.invalidateInk(); };
    function notify(kind) { if (api.onPlaying) api.onPlaying(kind); }
    function cancel() {
      if (frame) {
        if (api.cancelWetFrame) api.cancelWetFrame(frame);
        else cancelAnimationFrame(frame);
      }
      clearTimeout(wake); frame = wake = 0;
    }
    function request() {
      clearTimeout(wake); wake = 0;
      if (!destroyed && !paused && active && !frame) frame = api.requestWetFrame ? api.requestWetFrame(tick) : requestAnimationFrame(tick);
    }
    function damage(bounds) {
      outputDirty = true;
      for (const c of cards) if (c.ly && (!bounds || (bounds.left < c.clip.right && bounds.right > c.clip.left && bounds.top < c.clip.bottom && bounds.bottom > c.clip.top))) c.dirty = true;
    }
    // Dry cards hold no pane: their layers go, the page's touch gestures are its own again, and a wipe has nothing to clear.
    function setCardWet(v) {
      if (v !== cardWet) {
        cardWet = v;
        if (!v) {
          cancelPointer(); dryAt = Infinity;
          for (const el of [...layers.keys()]) removeLayer(el);
          for (const c of cards) { c.ly = null; c.dirty = false; }
        } else for (const { box } of layers.values()) box.style.opacity = v < 1 ? String(v) : '';
      }
      html.classList.toggle('sky-wet-active', active && cardWet > 0);
    }
    function dry(now, ms) { if (cardWet > 0) { dryAt = now; dryMs = ms; dryFrom = cardWet; } }

    function makeGrain() {
      const c = canvas(), size = FOG_GRAIN.tile, x = c.getContext('2d');
      c.width = c.height = size;
      for (let i = 0; i < FOG_GRAIN.count; i++) {
        const at = () => FOG_GRAIN.radius + Math.random() * (size - 2 * FOG_GRAIN.radius);
        const px = at(), py = at(); grainPoints.push([px, py]);
        x.beginPath(); x.arc(px, py, FOG_GRAIN.radius, 0, TAU); x.fill();
      }
      backend?.upload(3, c);
      return x.getImageData(0, 0, size, size);
    }
    function readTokens() {
      const s = getComputedStyle(html), rgb = name => parse(s.getPropertyValue(name));
      tokens = { milk: rgb('--wet-fog-rgb'), shift: rgb('--wet-card-shift-rgb'), shade: rgb('--wet-rim-rgb'), light: rgb('--wet-light-rgb'),
        skyAlpha: Number(s.getPropertyValue('--wet-sky-alpha')), cardAlpha: Number(s.getPropertyValue('--wet-card-alpha')),
        cardMilk: Number(s.getPropertyValue('--wet-card-milk')),
        saturation: Number(s.getPropertyValue('--glass-saturation')) };
      tokensDirty = false;
    }
    function removeLayer(el) { const ly = layers.get(el); if (ly) { ly.box.remove(); ly.cv.width = ly.cv.height = 1; layers.delete(el); } }
    function layerFor(el) {
      let ly = layers.get(el);
      if (!ly) {
        const box = document.createElement('div'), cv = canvas(); box.className = 'sky-wet-pane'; box.setAttribute('aria-hidden', 'true'); box.appendChild(cv); el.appendChild(box);
        if (cardWet < 1) box.style.opacity = String(cardWet);
        ly = { box, cv, x: cv.getContext('2d') }; layers.set(el, ly);
      }
      return ly;
    }
    function readLayout() {
      cards = [];
      const seen = new Set(), panes = new Set(document.querySelectorAll(CARD_SELECTOR));
      for (const el of observed) if (!panes.has(el)) { sizes.unobserve(el); observed.delete(el); }
      for (const el of panes) {
        if (!observed.has(el)) { sizes.observe(el); observed.add(el); }
        const r = el.getBoundingClientRect();
        if (el.hidden || !el.getClientRects().length || r.width <= 0 || r.height <= 0 || r.right <= 0 || r.left >= W || r.bottom <= 0 || r.top >= H) continue;
        const clip = { left: Math.max(0, r.left), top: Math.max(0, r.top), right: Math.min(W, r.right), bottom: Math.min(H, r.bottom) };
        clip.width = clip.right - clip.left; clip.height = clip.bottom - clip.top;
        const ly = cardWet > 0 ? layerFor(el) : null;
        if (ly) { seen.add(el); Object.assign(ly.cv.style, { left: `${clip.left - r.left}px`, top: `${clip.top - r.top}px`, width: `${clip.width}px`, height: `${clip.height}px` }); }
        cards.push({ el, r, clip, ly, dirty: !!ly, summary: el.matches('.sum-card'),
          round: Math.min(parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0, r.width / 2, r.height / 2) });
      }
      // Include a pixel of rounding slack per axis, so the aggregate cap also holds for many small panes.
      const area = cards.reduce((total, c) => total + !!c.ly * (c.clip.width * scale + 1) * (c.clip.height * scale + 1), 0);
      const cardScale = scale * Math.min(1, Math.sqrt(CARD_PIXEL_MAX / Math.max(1, area)));
      for (const c of cards) {
        if (!c.ly) continue;
        const width = Math.max(1, Math.floor(c.clip.width * cardScale)), height = Math.max(1, Math.floor(c.clip.height * cardScale));
        if (c.ly.cv.width !== width) c.ly.cv.width = width;
        if (c.ly.cv.height !== height) c.ly.cv.height = height;
      }
      for (const el of layers.keys()) if (!seen.has(el)) removeLayer(el);
      underCard = new Uint8Array(fw * fh);
      for (const c of cards) {
        const i0 = Math.max(0, Math.floor(c.clip.left * softScale)), i1 = Math.min(fw, Math.ceil(c.clip.right * softScale));
        for (let j = Math.max(0, Math.floor(c.clip.top * softScale)), j1 = Math.min(fh, Math.ceil(c.clip.bottom * softScale)); j < j1; j++) underCard.fill(1, j * fw + i0, j * fw + Math.max(i0, i1));
      }
      glyphsDirty = beadsDirty = true;
      const pane = cards.find(c => c.summary), row = pane && document.querySelector('.sum-card .sx-row[data-item-id="rh"] .sx-do');
      const r = row?.getBoundingClientRect();
      const nextAction = r?.width && r.height && r.bottom > 0 && r.top < H ? { r, pane } : null;
      // The wipe texture is in viewport coordinates. Never leave an old swath behind after a layout move.
      if (lastHand && (!nextAction || action.pane.el !== pane.el || ['left', 'right', 'top', 'bottom'].some(
        edge => Math.abs(action.r[edge] - r[edge]) > LAYOUT_MOVE_PX || Math.abs(action.pane.r[edge] - pane.r[edge]) > LAYOUT_MOVE_PX))) resetWipe(true);
      action = nextAction;
      layoutDirty = false; materialDirty = true; fogDirty = true;
    }
    function sprite(radius) {
      const r = Math.round(radius / SPRITE_RADIUS_STEP) * SPRITE_RADIUS_STEP;
      if (sprites.has(r)) return sprites.get(r);
      const c = canvas(), n = Math.max(SPRITE_MIN_PX, Math.ceil(r * 2 * SPRITE_SCALE)); c.width = c.height = n;
      const x = c.getContext('2d'), im = x.createImageData(n, n);
      for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
        const dx = (i + .5) / n * 2 - 1, dy = (j + .5) / n * 2 - 1, q = Math.hypot(dx, dy), p = (j * n + i) * 4;
        im.data[p] = (dx * .5 + .5) * 255; im.data[p + 1] = (dy * .5 + .5) * 255;
        im.data[p + 2] = r / LENS_RADIUS_PX * 255; im.data[p + 3] = clamp((1 - q) * n / 2) * 255;
      }
      x.putImageData(im, 0, 0); sprites.set(r, c); return c;
    }
    // Mark the cells under every word on screen (text nodes' line boxes), read only when the beads are redrawn after a layout change.
    function readGlyphs() {
      glyphW = Math.ceil(W / GLYPH_CELL_PX); glyphH = Math.ceil(H / GLYPH_CELL_PX); glyphs = new Uint8Array(glyphW * glyphH); glyphsDirty = false;
      const root = document.querySelector('.page') || document.body;
      if (!root || !document.createTreeWalker || !document.createRange) return;
      const walk = document.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */), range = document.createRange();
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        if (!n.data.trim()) continue;
        // Visually hidden text (screen-reader only, clipped to a pixel) lays out a full line box no one sees: it keeps no bead off.
        const host = n.parentElement, box = host?.getBoundingClientRect();
        if (!box || box.width < 2 || box.height < 2 || host.closest('.vh')) continue;
        range.selectNodeContents(n);
        const ink = (parseFloat(getComputedStyle(host).fontSize) || 16) * GLYPH_HEIGHT;
        for (const r of range.getClientRects()) {
          if (!r.width || !r.height || r.right < 0 || r.left > W || r.bottom < 0 || r.top > H) continue;
          const lead = Math.max(0, (r.height - ink) / 2), top = r.top + lead, bottom = r.bottom - lead;
          const i0 = Math.max(0, Math.floor((r.left - GLYPH_MARGIN_PX) / GLYPH_CELL_PX)), i1 = Math.min(glyphW - 1, Math.floor((r.right + GLYPH_MARGIN_PX) / GLYPH_CELL_PX));
          for (let j = Math.max(0, Math.floor((top - GLYPH_MARGIN_PX) / GLYPH_CELL_PX)), j1 = Math.min(glyphH - 1, Math.floor((bottom + GLYPH_MARGIN_PX) / GLYPH_CELL_PX)); j <= j1; j++) glyphs.fill(1, j * glyphW + i0, j * glyphW + i1 + 1);
        }
      }
    }
    function overWords(x, y, r) {
      const i0 = Math.max(0, Math.floor((x - r) / GLYPH_CELL_PX)), i1 = Math.min(glyphW - 1, Math.floor((x + r) / GLYPH_CELL_PX));
      for (let j = Math.max(0, Math.floor((y - r) / GLYPH_CELL_PX)), j1 = Math.min(glyphH - 1, Math.floor((y + r) / GLYPH_CELL_PX)); j <= j1; j++) for (let i = i0; i <= i1; i++) if (glyphs[j * glyphW + i]) return true;
      return false;
    }
    // A pass's beads form (start, then swell over their own time); any other set of drops is already there.
    function makeDrops(form = false) {
      drops = [];
      for (const [per, lo, hi] of BEAD_GROUPS) for (let i = 0, n = Math.min(BEAD_MAX - drops.length, Math.round(W * H / per)); i < n; i++) {
        drops.push({ x: Math.random() * W, y: Math.random() * H, r: lo + Math.random() ** BEAD_RADIUS_BIAS * (hi - lo),
          ...(form ? { at: within(BEAD_AT_MS), grow: within(BEAD_GROW_MS) } : {}) });
      }
      // Each card on screen gets at least its own share, so a phone's cards keep a dense field (words still keep theirs clear).
      for (const c of cards) {
        const { left, top, width, height } = c.clip, want = Math.max(CARD_BEADS.least, Math.round(width * height / CARD_BEADS.area));
        let have = drops.reduce((n, b) => n + (b.x >= left && b.x < left + width && b.y >= top && b.y < top + height ? 1 : 0), 0);
        for (const [per, lo, hi] = BEAD_GROUPS[1]; have < want && drops.length < BEAD_MAX * 1.5; have++) drops.push({ x: left + Math.random() * width, y: top + Math.random() * height,
          r: lo + Math.random() ** BEAD_RADIUS_BIAS * (hi - lo), ...(form ? { at: within(BEAD_AT_MS), grow: within(BEAD_GROW_MS) } : {}) });
      }
      forming = form; beadsDirty = true;
    }
    const viewPane = () => ({ r: { left: 0, top: 0, width: W, height: H }, round: 0, clip: { left: 0, top: 0, right: W, bottom: H } });
    const within = ([lo, hi]) => lo + Math.random() * (hi - lo);
    // How far a drop at x falls from y before a word stops it (straight down).
    const freeFall = (x, y, r) => { let v = y; while (v < H && !overWords(x, v, r + RUN_DODGE.gap)) v += GLYPH_CELL_PX; return v - y; };
    function makeRunners() {
      if (glyphsDirty) readGlyphs();
      const taken = [];
      runners = RUN_AT_MS.map(at => {
        const r = within(RUN_RADIUS_PX);
        let x = W / 2, y = H * RUN_START.top, best = -1;
        for (let k = 0; k < RUN_COLUMNS.tries; k++) {
          const cx = W * (RUN_START.left + RUN_START.width * Math.random()), cy = H * (RUN_START.top + Math.random() * RUN_START.height);
          if (taken.some(t => Math.abs(t - cx) < RUN_COLUMNS.apart)) continue;
          const fall = freeFall(cx, cy, r);
          if (fall > best) { best = fall; x = cx; y = cy; }
        }
        taken.push(x);
        return { x0: x, x, y, r, at, v: 0, vx: 0, goal: 0, wob: Math.random() * TAU };
      });
    }
    const runX = (rn, y) => rn.x0 + (rn.dx || 0) + RUN_WANDER[0] * Math.sin(y / RUN_WANDER[1] + rn.wob) + RUN_WANDER[2] * Math.sin(y / RUN_WANDER[3] + rn.wob * 2);
    // Is the drop's column at x clear of words from y down to y + reach?
    const clearRun = (x, y, r, reach) => { for (let v = y; v <= y + reach; v += GLYPH_CELL_PX / 2) if (overWords(x, v, r + RUN_DODGE.gap)) return false; return true; };
    // The detour (px aside from where the drop would run) that keeps its column clear ahead: the nearest, null if none.
    function dodge(rn, y) {
      const x = runX(rn, y), ahead = RUN_DODGE.ahead + rn.r;
      if (clearRun(x, y, rn.r, ahead)) return 0;
      for (let d = RUN_DODGE.probe; d <= RUN_DODGE.max; d += RUN_DODGE.probe) for (const side of rn.wob > Math.PI ? [1, -1] : [-1, 1]) {
        if ((rn.dx || 0) + side * d >= -RUN_DODGE.max && (rn.dx || 0) + side * d <= RUN_DODGE.max && clearRun(x + side * d, y, rn.r, ahead)) return side * d;
      }
      return null;
    }
    // Each runner slides down in fits and starts; behind its head the fog is cleared (held, then misting back).
    function stepRunners(t, dt, now) {
      for (const rn of runners) {
        if (t < rn.at || rn.done) continue;
        const sliding = Math.sin((t - rn.at) / RUN_CATCH.period + rn.wob) > RUN_CATCH.threshold;
        rn.v = sliding ? Math.min(RUN_SPEED.max, rn.v + dt * RUN_SPEED.accel) : rn.v * RUN_SPEED.brake;
        if (glyphsDirty) readGlyphs();
        // Step down in trail-sized steps, sliding aside as a word comes up; one continuous path, the bead at its head.
        const target = rn.y + rn.v * dt / 1000;
        for (let guard = 0; rn.y < target && !rn.done && guard < RUN_STEPS_MAX; guard++) {
          const step = Math.min(RUN_TRAIL.step, target - rn.y), aside = dodge(rn, rn.y);
          // A word ahead sets a new detour; the drop eases toward it on a spring, so the path curves.
          if (aside) rn.goal = (rn.dx || 0) + aside;
          rn.vx = (rn.vx + (rn.goal - (rn.dx || 0)) * RUN_DODGE.pull) * RUN_DODGE.damp; rn.dx = (rn.dx || 0) + rn.vx;
          const blocked = () => overWords(runX(rn, rn.y + step), rn.y + step, rn.r + RUN_DODGE.gap);
          if (aside !== null && blocked()) {
            // The curve lags behind a word that came up close: the drop slides aside before it goes on (still one path).
            const left = rn.goal - rn.dx, slide = Math.sign(left) * Math.min(Math.abs(left), step * RUN_DODGE.catchUp);
            rn.dx += slide; rn.vx = slide;
            if (blocked() && Math.abs(left) > step * RUN_DODGE.catchUp) continue;
          }
          if (aside === null || blocked()) {
            // No way past: it stops above the word and stays on the glass as a bead.
            rn.done = true; drops.push({ x: runX(rn, rn.y), y: rn.y, r: rn.r * .8 }); beadsDirty = true; break;
          }
          rn.y += step;
          stamp(viewPane(), runX(rn, rn.y), rn.y - rn.r * RUN_TRAIL.behind, rn.r * RUN_TRAIL.rx, rn.r * RUN_TRAIL.ry, now + RUN_TRAIL_HOLD_MS, true, REGROW_MS, true, WIPE_EDGE_PX);
        }
        rn.x = runX(rn, rn.y);
        if (rn.y - rn.r * RUN_SHAPE.tail[0] > H) rn.done = true;
      }
    }
    // A runner's lens: its body, stretched as it speeds up, and a narrower tail above it. Painted into the bead map.
    function runnerLens(rn, t) {
      const grow = ease((t - rn.at + RUN_GROW_MS) / RUN_GROW_MS), r = rn.r * (.45 + .55 * grow), st = clamp(rn.v / RUN_SPEED.max);
      const [offset, offsetSpeed, length, lengthSpeed, half] = RUN_SHAPE.tail, body = 1 + st * RUN_SHAPE.stretch;
      const top = rn.y - r * (offset + st * offsetSpeed), bottom = rn.y + r * body;
      return { owner: rn, x: rn.x, y: (top + bottom) / 2, r: Math.max(r, (bottom - top) / 2), alpha: 1, paint() {
        const lens = sprite(r);
        beadX.drawImage(lens, (rn.x - r * half) * scale, top * scale, r * 2 * half * scale, r * (length + st * lengthSpeed) * scale);
        beadX.drawImage(lens, (rn.x - r) * scale, (rn.y - r * body) * scale, r * 2 * scale, r * 2 * body * scale);
      } };
    }
    // A forming bead's share of its size: only while a pass runs; a settled or stopped pane shows them whole.
    const formed = (b, now) => b.at == null || !['pass', 'thin', 'stop'].includes(mode) ? 1 : ease((now - passAt - b.at) / b.grow);
    function stamp(pane, x, y, rx, ry, until, bounded = false, regrow = REGROW_MS, trail = false, edgePx = WIPE_EDGE_SOFT_PX) {
      const reach = bounded ? 1 : 1 + PALM_EDGE.wave + PALM_EDGE.fine;
      const { clip } = pane;
      const left = Math.floor(Math.max(clip.left, x - rx * reach) * softScale);
      const right = Math.min(fw - 1, Math.floor(Math.min(clip.right, x + rx * reach) * softScale));
      const top = Math.floor(Math.max(clip.top, y - ry * reach) * softScale);
      const bottom = Math.min(fh - 1, Math.floor(Math.min(clip.bottom, y + ry * reach) * softScale));
      for (let j = top; j <= bottom; j++) for (let i = left; i <= right; i++) {
        const px = (i + .5) / softScale, py = (j + .5) / softScale;
        // A soft texel of inset keeps the interpolated wipe inside the card's rounded edge.
        const clipped = smooth(-paneDistance(pane, px, py) * softScale - .5);
        if (!clipped) continue;
        const dx = (px - x) / rx, dy = (py - y) / ry;
        const a = Math.atan2(dy, dx), radial = Math.hypot(dx, dy);
        const scalloped = radial * (1 + PALM_EDGE.wave * Math.sin(a * PALM_EDGE.lobes + PALM_EDGE.phase) + PALM_EDGE.fine * Math.sin(a * PALM_EDGE.fineLobes));
        const q = bounded ? Math.max(radial, scalloped) : scalloped;
        const soft = Math.min(edgePx / rx, 1), k = clipped * (1 - smooth((q - (1 - soft)) / soft)), o = j * fw + i;
        if (k > 0) {
          cleared[o] = Math.max(cleared[o], k); holdUntil[o] = Math.max(holdUntil[o], until); regrowFor[o] = regrow;
          if (trail) trailMark[o] = 1;
        }
      }
      // Push the same lenses out of the cleared swath as the hand passes. Later stamps can push them farther.
      for (const b of drops) {
        if (b.r < PUSH_MIN_R) continue;
        const i = Math.floor(b.x * softScale), by = b.pushedY ?? b.y, j = Math.floor(by * softScale);
        if (i < left || i > right || j < top || j > bottom || cleared[j * fw + i] <= LENS_WIPE[0]) continue;
        const side = by < y ? -1 : 1;
        for (const dir of [side, -side]) {
          let edge = j + dir;
          while (edge >= 0 && edge < fh && cleared[edge * fw + i] > LENS_WIPE[0]) edge += dir;
          const next = (edge + .5) / softScale + dir * (b.r + EDGE_BEAD_GAP_PX);
          if (paneDistance(pane, b.x, next) > -b.r) continue;
          b.pushedY = next; b.pushedUntil = Math.max(b.pushedUntil ?? 0, until); b.pushedAlpha = 1; beadsDirty = true; break;
        }
      }
      fogDirty = true; fogBusy = true;
    }
    function releasePointer() {
      if (pointerState?.wiping) {
        // Infinity holds just this stroke and its displaced beads during the drag.
        const until = performance.now() + WIPE_HOLD_MS;
        for (let i = 0; i < holdUntil.length; i++) if (holdUntil[i] === Infinity) holdUntil[i] = until;
        for (const b of drops) if (b.pushedUntil === Infinity) b.pushedUntil = until;
        fogDirty = true; request();
      }
      pointerState = null;
      html.classList.remove('sky-wet-dragging');
    }
    function cancelPointer() { releasePointer(); contacts.clear(); }
    function pointer(e) {
      if (!active || paused || destroyed || !cardWet) { cancelPointer(); return null; }
      if (e.type === 'pointercancel' || e.type === 'lostpointercapture') {
        contacts.delete(e.pointerId);
        if (pointerState?.id === e.pointerId) releasePointer();
        return null;
      }
      if (e.type === 'pointerdown') {
        contacts.add(e.pointerId);
        if (contacts.size > 1 || (e.pointerType === 'touch' && !e.isPrimary)) { releasePointer(); return null; }
        if (e.button !== 0 || e.target?.closest?.(CONTROL_SELECTOR)) return null;
        releasePointer();
        pointerState = { id: e.pointerId, type: e.pointerType === 'mouse' ? 'mouse' : 'touch',
          sx: e.clientX, sy: e.clientY, x: e.clientX, y: e.clientY, at: e.timeStamp, wiping: false };
        // Applied only to the current gesture, and removed on every end/cancel/lifecycle path.
        html.classList.add('sky-wet-dragging');
        return 'pending';
      }
      const p = pointerState;
      const ending = e.type === 'pointerup';
      if ((!ending && e.type !== 'pointermove') || !p || p.id !== e.pointerId || contacts.size !== 1) {
        if (ending) contacts.delete(e.pointerId);
        return null;
      }
      const finish = result => { if (ending) { contacts.delete(e.pointerId); releasePointer(); } return result; };
      if (!p.wiping) {
        const dx = e.clientX - p.sx, dy = e.clientY - p.sy;
        if (Math.hypot(dx, dy) < FINGER_THRESHOLD[p.type]) return finish(ending ? 'skip' : 'pending');
        if (Math.abs(dx) <= Math.abs(dy) * FINGER_HORIZONTAL_RATIO) { releasePointer(); return finish('skip'); }
        p.wiping = true;
      }
      if (e.cancelable) e.preventDefault();
      if (layoutDirty) readLayout();
      if (reduced()) frozen = true;
      const distance = Math.hypot(e.clientX - p.x, e.clientY - p.y);
      const speed = distance / Math.max(FINGER_MIN_DT_MS, e.timeStamp - p.at);
      const [narrow, wide] = FINGER_WIDTH[p.type], radius = (wide + (narrow - wide) * clamp(speed / FINGER_FAST_SPEED)) / 2;
      const pane = { r: { left: 0, top: 0, width: W, height: H }, round: 0, clip: { left: 0, top: 0, right: W, bottom: H } };
      const steps = Math.max(1, Math.ceil(distance / WIPE_STEP_PX));
      for (let i = 0; i <= steps; i++) stamp(pane, p.x + (e.clientX - p.x) * i / steps,
        p.y + (e.clientY - p.y) * i / steps, radius, radius, Infinity, true);
      p.x = e.clientX; p.y = e.clientY; p.at = e.timeStamp;
      request();
      return finish('wipe');
    }
    function hand(t, now) {
      if (!action || t < WIPE_AT_MS || lastHand?.done) return;
      const { r, pane } = action;
      const k = clamp((t - WIPE_AT_MS) / WIPE_DURATION_MS), e = smooth(k);
      // Palm-wide along the action's line, from inside the card's edge to just past its words; it presses in and lifts off.
      const x0 = Math.max(pane.clip.left, pane.r.left + WIPE_INSET_PX), x1 = Math.min(pane.clip.right, pane.r.right - WIPE_INSET_PX, r.right + WIPE_PAST_TEXT_PX);
      const textReach = r.height / 2 + WIPE_ARC_PX + WIPE_TILT_PX / 2 + WIPE_EDGE_SOFT_PX;
      const at = u => ({ x: x0 + (x1 - x0) * u, y: r.top + r.height / 2 - Math.sin(u * Math.PI) * WIPE_ARC_PX + (u - .5) * WIPE_TILT_PX });
      const p = at(e), from = lastHand || at(0), n = Math.max(1, Math.ceil(Math.hypot(p.x - from.x, p.y - from.y) / WIPE_STEP_PX));
      for (let i = 1; i <= n; i++) {
        const x = from.x + (p.x - from.x) * i / n;
        const u = (x - x0) / Math.max(1, x1 - x0), press = PALM_TAPER.least + (1 - PALM_TAPER.least) * smooth(u / PALM_TAPER.land) * smooth((1 - u) / PALM_TAPER.lift);
        const ry = Math.max(textReach, WIPE_RADIUS_Y) * press * (1 + PALM_WIDTH_VARIATION * (Math.sin(x / PALM_WAVE_PX[0]) + Math.sin(x / PALM_WAVE_PX[1] + 1.3)) / 2);
        stamp(pane, x, from.y + (p.y - from.y) * i / n, WIPE_RADIUS_X, ry, now + WIPE_HAND_HOLD_MS, false, HAND_REGROW_MS);
      }
      lastHand = { ...p, done: k === 1 };
    }
    // The breath blooms up from below the bottom centre: a wide soft front, its opacity coming in with it (no white frame).
    const bloomAt = (o, t) => smooth((smooth(t / FOG_GROW_MS) * (1 + BLOOM.soft) - bloomD[o]) / BLOOM.soft) * smooth(t / (FOG_GROW_MS * BLOOM.fade));
    // How much fog is left at clearing progress k: the frame clears first, the centre last, along a lumpy soft edge.
    const keepAt = (o, k) => smooth((edgeE[o] - (k * (1 + CLEAR_SOFT) - CLEAR_SOFT)) / CLEAR_SOFT);
    function fogAt(o, y, now) {
      const film = settledFilm;
      const rest = film + (SETTLED_EDGE_FOG - film) * smooth((y / H - 1 + SETTLED_BOTTOM) / SETTLED_BOTTOM);
      const t = now - passAt;
      const growth = mode === 'pass' ? bloomAt(o, t) : mode === 'thin' ? keepAt(o, clamp((t - FOG_THIN_AT_MS) / FOG_THIN_MS)) : amount;
      return rest + (1 - rest) * growth;
    }
    // The breath over the words (C's front fog): the same bloom, held, then lifting from the edges in; a wipe clears it too.
    function frontPattern(dark) {
      const key = `${dark}:${frontScale}`;
      if (frontGrainKey === key) return frontGrain;
      const tile = canvas(), size = Math.max(1, Math.round(FRONT_GRAIN.tile * frontScale)), x = tile.getContext('2d'), [shadow, lit] = FRONT_GRAIN_INK[dark ? 'dark' : 'light'];
      tile.width = tile.height = size; x.scale(size / FRONT_GRAIN.tile, size / FRONT_GRAIN.tile);
      for (let i = 0; i < FRONT_GRAIN.count; i++) {
        const px = Math.random() * FRONT_GRAIN.tile, py = Math.random() * FRONT_GRAIN.tile, r = FRONT_GRAIN.radius[0] + Math.random() ** FRONT_GRAIN.radius[2] * FRONT_GRAIN.radius[1];
        x.fillStyle = colour(shadow.slice(0, 3), shadow[3]); x.beginPath(); x.arc(px + r * .35, py + r * .45, r, 0, TAU); x.fill();
        x.fillStyle = colour(lit.slice(0, 3), lit[3]); x.beginPath(); x.arc(px, py, r * .8, 0, TAU); x.fill();
      }
      frontGrain = frontX.createPattern(tile, 'repeat'); frontGrainKey = key; return frontGrain;
    }
    function hideFront() { if (frontC && frontShown) { frontX.clearRect(0, 0, frontC.width, frontC.height); frontShown = false; } }
    function drawFront(now) {
      if (!frontC) return;
      const stopping = mode === 'stop', t = (stopping ? stopAt : now) - passAt, fade = stopping ? 1 - smooth((now - stopAt) / SKIP_FADE_MS) : 1;
      if (!['pass', 'thin', 'stop'].includes(mode) || fade <= 0 || t >= FRONT_CLEAR_AT_MS + FRONT_CLEAR_MS) { hideFront(); return; }
      const dark = !!api.dark?.(), peak = FRONT_PEAK[dark ? 'dark' : 'light'] * fade, rgb = FRONT_RGB[dark ? 'dark' : 'light'];
      const k = clamp((t - FRONT_CLEAR_AT_MS) / FRONT_CLEAR_MS), d = frontImage.data, lump = FOG_PATTERN.body + FOG_PATTERN.wave + FOG_PATTERN.lower;
      for (let o = 0, p = 0; o < fw * fh; o++, p += 4) {
        const a = bloomAt(o, t) * keepAt(o, k) * base[o] / lump * (1 - fogImage.data[p + 1] / 255) * peak;
        d[p] = rgb[0]; d[p + 1] = rgb[1]; d[p + 2] = rgb[2]; d[p + 3] = a * 255;
      }
      frontMaskX.putImageData(frontImage, 0, 0);
      frontX.clearRect(0, 0, frontC.width, frontC.height); frontX.drawImage(frontMaskC, 0, 0, frontC.width, frontC.height);
      // C's fine droplets, only where the breath is, as dense as it is.
      frontX.globalCompositeOperation = 'source-atop'; frontX.globalAlpha = FRONT_GRAIN.alpha;
      frontX.fillStyle = frontPattern(dark); frontX.fillRect(0, 0, frontC.width, frontC.height);
      frontX.globalCompositeOperation = 'source-over'; frontX.globalAlpha = 1; frontShown = true;
    }
    function updateFog(now) {
      settledFilm = SETTLED_FILM[api.dark?.() ? 'dark' : 'light']; // read once per map update, not per cell
      fogBusy = regrowing = false; nextRegrowAt = Infinity;
      let first = fh, last = -1, left = fw, right = -1;
      for (let j = 0; j < fh; j++) for (let i = 0; i < fw; i++) {
        const o = j * fw + i, p = o * 4;
        if (cleared[o] > 0 && !frozen) {
          if (now >= holdUntil[o] + regrowFor[o]) cleared[o] = trailMark[o] = 0;
          else {
            fogBusy = true;
            if (now >= holdUntil[o]) regrowing = true;
            else nextRegrowAt = Math.min(nextRegrowAt, holdUntil[o]);
          }
        }
        const wipe = cleared[o] * (frozen ? 1 : 1 - smooth((now - holdUntil[o]) / (regrowFor[o] || REGROW_MS)));
        const fogByte = Math.round(fogAt(o, (j + .5) / softScale, now) * base[o] * 255), wipeByte = Math.round(wipe * 255), skyByte = underCard[o] ? 0 : wipeByte, keepByte = 255 - wipeByte * trailMark[o];
        if (fogImage.data[p] !== fogByte || fogImage.data[p + 1] !== wipeByte || fogImage.data[p + 2] !== skyByte || fogImage.data[p + 3] !== keepByte) {
          first = Math.min(first, j); last = j; left = Math.min(left, i); right = Math.max(right, i);
        }
        fogImage.data[p] = fogByte; fogImage.data[p + 1] = wipeByte; fogImage.data[p + 2] = skyByte; fogImage.data[p + 3] = keepByte;
      }
      if (backend && last >= first) backend.upload(0, fogC, fogImage.data, [first, last]);
      // Include a soft texel of interpolation around changed pixels.
      if (last >= first) damage({ left: (left - 1) / softScale, right: (right + 2) / softScale, top: (first - 1) / softScale, bottom: (last + 2) / softScale });
      fogDirty = false;
    }
    function updateDisplaced(now) {
      for (const b of drops) {
        if (b.pushedY == null) continue;
        const o = Math.floor(b.y * softScale) * fw + Math.floor(b.x * softScale);
        const until = Math.max(b.pushedUntil, holdUntil[o] || 0);
        const alpha = frozen ? 1 : 1 - smooth((now - until) / REGROW_MS);
        if (Math.round(alpha * 255) !== Math.round(b.pushedAlpha * 255)) beadsDirty = true;
        b.pushedAlpha = alpha;
        if (!frozen && alpha > 0) {
          fogBusy = true;
          if (now >= until) regrowing = true;
          else nextRegrowAt = Math.min(nextRegrowAt, until);
        }
        if (!alpha) { delete b.pushedY; delete b.pushedUntil; delete b.pushedAlpha; beadsDirty = true; }
      }
    }
    function copyScene() {
      const s = api.sky(), g = viewX.createLinearGradient(0, 0, 0, viewC.height);
      g.addColorStop(0, colour(s.top)); g.addColorStop(1, colour(s.hor)); viewX.fillStyle = g; viewX.fillRect(0, 0, viewC.width, viewC.height);
      // Copy only the rear sources. The compositor will sample rain and flashes after wet.
      for (const { c, opacity } of sceneSources) {
        if (!c.width || !c.height || c.hidden) continue;
        try { viewX.globalAlpha = c.style.opacity === '' ? opacity : Number(c.style.opacity); viewX.drawImage(c, 0, 0, viewC.width, viewC.height); } catch (_) { /* An unavailable weather canvas leaves the gradient intact. */ }
      }
      viewX.globalAlpha = 1; viewImage = viewX.getImageData(0, 0, viewC.width, viewC.height);
      if (backend) backend.upload(2, viewC); sceneDirty = false;
    }
    function drawBeads(now) {
      const previous = rasterDrops, originals = new Map(), displaced = new Map();
      for (const b of previous) (b.displaced ? displaced : originals).set(b.owner, b);
      rasterDrops = [];
      beadX.clearRect(0, 0, beadC.width, beadC.height);
      const k = burstDrops.length ? clamp((now - burstAt) / BURST_MS) : 0;
      let stillForming = false;
      if (glyphsDirty) readGlyphs();
      for (const b of [...drops, ...burstDrops]) {
        // The original lens is masked by the wipe and reappears with the mist; its displaced twin fades at the edge.
        const size = formed(b, now); if (size < 1) stillForming = true;
        const y = b.y + (b.burst ? k * BURST_TRAVEL_PX : 0);
        if (!overWords(b.x, y, b.r)) rasterDrops.push({ owner: b, x: b.x, y, r: b.r * size, alpha: b.burst ? Math.sin(k * Math.PI) : 1 });
        if (b.pushedY != null && !overWords(b.x, b.pushedY, b.r)) rasterDrops.push({ owner: b, displaced: true, x: b.x, y: b.pushedY, r: b.r * size * PUSHED_GROW, alpha: b.pushedAlpha });
      }
      forming = stillForming; formedAt = now;
      for (const rn of runners) if (!rn.done && now - passAt >= rn.at - RUN_GROW_MS) rasterDrops.push(runnerLens(rn, now - passAt));
      const changed = b => damage({ left: b.x - b.r, right: b.x + b.r, top: b.y - b.r, bottom: b.y + b.r });
      for (const b of rasterDrops) {
        if (b.r < SPRITE_RADIUS_STEP) continue; // a bead not yet formed has no lens to draw
        beadX.globalAlpha = b.alpha;
        if (b.paint) b.paint(); else beadX.drawImage(sprite(b.r), (b.x - b.r) * scale, (b.y - b.r) * scale, b.r * 2 * scale, b.r * 2 * scale);
        const oldMap = b.displaced ? displaced : originals, old = oldMap.get(b.owner);
        if (!old || old.x !== b.x || old.y !== b.y || old.r !== b.r || Math.round(old.alpha * 255) !== Math.round(b.alpha * 255)) { if (old) changed(old); changed(b); }
        oldMap.delete(b.owner);
      }
      for (const b of [...originals.values(), ...displaced.values()]) changed(b);
      beadX.globalAlpha = 1;
      beadImage = null; // read back only when sampled (ink, the 2D path): runners redraw the map every frame
      if (backend) backend.upload(1, beadC); beadsDirty = false;
    }
    const beadPixels = () => beadImage ||= beadX.getImageData(0, 0, beadC.width, beadC.height);
    function drawGpu(c) {
      const { gl, U } = backend, m = c?.material;
      gl.viewport(0, 0, output.width, output.height); gl.enable(gl.SCISSOR_TEST);
      const r = c?.clip || { left: 0, top: 0, right: W, bottom: H };
      const left = Math.floor(r.left * scale), bottom = Math.floor((H - r.bottom) * scale);
      gl.scissor(left, bottom, Math.min(output.width - left, Math.ceil((r.right - r.left) * scale)), Math.min(output.height - bottom, Math.ceil((r.bottom - r.top) * scale)));
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(U.px, 1 / W, 1 / H);
      gl.uniform4f(U.bounds, c?.r.left || 0, c?.r.top || 0, c?.r.right || W, c?.r.bottom || H);
      gl.uniform1f(U.amount, amount); gl.uniform1f(U.cardMilk, tokens.cardMilk); gl.uniform1f(U.skyFog, tokens.skyAlpha); gl.uniform1f(U.restFog, settledFilm * FOG_PATTERN.body); gl.uniform2f(U.trailShade, ...TRAIL_SHADE[api.dark?.() ? 'dark' : 'light']); gl.uniform1f(U.beadLit, BEAD_LIT[api.dark?.() ? 'dark' : 'light']);
      for (const key of ['milk', 'shift', 'shade', 'light']) gl.uniform3fv(U[key], tokens[key].map(v => v / 255));
      gl.uniform3fv(U.glass, (m?.rgb || tokens.milk).map(v => v / 255));
      gl.uniform1f(U.glassAlpha, m?.alpha ?? 0); gl.uniform1f(U.brightness, m?.backdropBrightness ?? 1);
      gl.uniform1f(U.saturation, tokens.saturation);
      gl.uniform1f(U.card, c ? 1 : 0); gl.uniform1f(U.fogAlpha, c ? tokens.cardAlpha : tokens.skyAlpha);
      gl.uniform1f(U.beadAlpha, BEAD_SETTLED_ALPHA + (1 - BEAD_SETTLED_ALPHA) * amount);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    // Both backends use the same material and lens optics; only their rasterization differs.
    function paneColour(at, m) {
      if (!m) return at.slice(0, 3);
      const grey = MATERIAL_LUMA.reduce((v, weight, i) => v + at[i] * weight, 0);
      return m.rgb.map((v, k) => {
        const bg = clamp((grey + (at[k] - grey) * tokens.saturation) * m.backdropBrightness, 0, 255);
        return bg + (v - bg) * m.alpha;
      });
    }
    const wipeOf = (c, f) => f[c ? 1 : 2] / 255; // a card's wipe, or the open sky's (never wiped under a card)
    function fogDensity(y, c, f) {
      const local = c ? Math.max(SETTLED_CARD_FILM, SETTLED_EDGE_FOG * smooth((y - c.r.bottom + SETTLED_EDGE_PX) / SETTLED_EDGE_PX)) * (1 - amount) : 0;
      return Math.max(f[0] / 255, local) * (1 - wipeOf(c, f));
    }
    function fogLayer(x, y, c, f, includeGrain = true) {
      const m = c?.material;
      const grain = includeGrain ? texture(grainImage, x % FOG_GRAIN.tile, y % FOG_GRAIN.tile, 1)[3] / 255 * (SETTLED_GRAIN + (1 - SETTLED_GRAIN) * amount) * FOG_GRAIN.lift : 0;
      // The shader's: the settled film takes the card's own colour (its glass over the sky's fog); the pass's fog is milky.
      const view = m ? texture(viewImage, x, y, softScale) : null;
      const own = m ? paneColour(view.map((v, k) => k < 3 ? v + (tokens.milk[k] - v) * tokens.skyAlpha * f[0] / 255 * (1 - f[2] / 255) : v), m) : null;
      const milk = own ? tokens.milk.map((v, k) => {
        const tint = clamp(own[k] + tokens.shift[k], 0, 255);
        const haze = own[k] + (v - own[k]) * SETTLED_HAZE;
        return haze + (tint + (v - tint) * tokens.cardMilk - haze) * amount;
      }) : tokens.milk;
      const density = fogDensity(y, c, f), alpha = density * (c ? tokens.cardAlpha : tokens.skyAlpha), fleck = grain * density;
      const fog = [...milk.map((v, k) => v * alpha * (1 - fleck) + tokens.light[k] * fleck), alpha + fleck * (1 - alpha)];
      const rest = settledFilm * FOG_PATTERN.body, excess = m ? clamp((f[0] / 255 - rest) / Math.max(1 - rest, .01)) * amount * f[1] / 255 : 0;
      const bare = excess ? paneColour(view.map((v, k) => k < 3 ? v + (tokens.milk[k] - v) * tokens.skyAlpha * rest : v), m) : null;
      const swath = excess ? [...bare.map((v, k) => v * excess + fog[k] * (1 - excess)), excess + fog[3] * (1 - excess)] : fog;
      const trail = 1 - f[3] / 255, [keep, sky] = TRAIL_SHADE[api.dark?.() ? 'dark' : 'light'];
      if (!trail) return swath;
      if (!m) { const a = trail * sky; return [...swath.slice(0, 3).map(v => v * (1 - a)), a + swath[3] * (1 - a)]; }
      const clear = paneColour(view, { ...m, alpha: m.alpha * TRAIL_GLASS }).map(v => v * keep);
      return [...clear.map((v, k) => v * trail + swath[k] * (1 - trail)), trail + swath[3] * (1 - trail)];
    }
    function lensLayer(x, y, c, f, b) {
      const edge = (a, z, v) => smooth((v - a) / (z - a));
      const dm = b[3] / 255 * (BEAD_SETTLED_ALPHA + (1 - BEAD_SETTLED_ALPHA) * amount) * (1 - edge(...LENS_WIPE, wipeOf(c, f)));
      if (!dm) return { clear: 0, rgba: [0, 0, 0, 0] };
      const nx = (b[0] / 255 - .5) * 2, ny = (b[1] / 255 - .5) * 2, rr = Math.hypot(nx, ny), r = b[2] / 255 * LENS_RADIUS_PX;
      const shift = r * (LENS.refract + rr * rr * LENS.curve);
      const inverted = texture(viewImage, x - nx * shift, y - ny * shift, softScale);
      let alpha = 0, pm = [0, 0, 0];
      const over = (rgb, a) => { pm = pm.map((v, k) => rgb[k] * a + v * (1 - a)); alpha = a + alpha * (1 - a); };
      let body = paneColour(inverted, c?.material);
      if (!c) {
        const fog = fogLayer(x, y, null, f, false), here = texture(viewImage, x, y, softScale);
        body = mix(body, here.slice(0, 3).map((v, k) => v * (1 - fog[3]) + fog[k]), LENS.fogMix * (1 - rr));
      }
      over(body, dm * (c ? LENS.cardBody : LENS.body));
      over(tokens.light, dm * BEAD_LIT[api.dark?.() ? 'dark' : 'light'] * (1 - edge(...BEAD_LIT.edge, rr)));
      over(tokens.shade, dm * edge(...LENS_RIM.slice(0, 2), rr) * edge(...LENS_RIM.slice(2), ny) * (c ? LENS.cardRim : LENS.rim));
      const hl = (1 - edge(...LENS_HIGHLIGHT.slice(0, 2), Math.hypot(nx - LENS_HIGHLIGHT[2], ny - LENS_HIGHLIGHT[3]))) * edge(...LENS_HIGHLIGHT.slice(4), r);
      const foot = edge(...LENS_FOOT.slice(0, 2), ny) * (1 - edge(...LENS_FOOT.slice(2, 4), rr)) * edge(...LENS_FOOT.slice(4), rr);
      over(tokens.light, dm * (hl * LENS.highlight + foot * LENS.foot));
      return { clear: dm * (c ? LENS.cardClear : LENS.clear), rgba: [...pm, alpha] };
    }
    function draw2d(target, c) {
      const x = target.getContext('2d'), clip = c?.clip || { left: 0, top: 0, right: W, bottom: H, width: W, height: H };
      const im = fallbackImage;
      const i0 = Math.max(0, Math.floor(clip.left * softScale) - 1), i1 = Math.min(fw, Math.ceil(clip.right * softScale) + 1);
      const j0 = Math.max(0, Math.floor(clip.top * softScale) - 1), j1 = Math.min(fh, Math.ceil(clip.bottom * softScale) + 1);
      for (let j = j0; j < j1; j++) for (let i = i0; i < i1; i++) {
        const p = (j * fw + i) * 4;
        const layer = fogLayer((i + .5) / softScale, (j + .5) / softScale, c, fogImage.data.subarray(p, p + 4), false);
        for (let k = 0; k < 3; k++) im.data[p + k] = layer[3] ? layer[k] / layer[3] : 0;
        im.data[p + 3] = layer[3] * 255;
      }
      fogX.putImageData(im, 0, 0); x.clearRect(0, 0, target.width, target.height);
      x.drawImage(fogC, clip.left * softScale, clip.top * softScale, clip.width * softScale, clip.height * softScale, 0, 0, target.width, target.height);
      // Reuse small tiles instead of another full-viewport copy. Normal-map pixels are shared with WebGL.
      const sx = target.width / clip.width, sy = target.height / clip.height;
      x.save(); x.scale(sx, sy); x.translate(-clip.left, -clip.top);
      // Rasterize microdroplets at CSS resolution, rather than magnifying quarter-resolution grain into blotches.
      const lift = (SETTLED_GRAIN + (1 - SETTLED_GRAIN) * amount) * FOG_GRAIN.lift;
      for (let ty = Math.floor(clip.top / FOG_GRAIN.tile) * FOG_GRAIN.tile; ty < clip.bottom; ty += FOG_GRAIN.tile) {
        for (let tx = Math.floor(clip.left / FOG_GRAIN.tile) * FOG_GRAIN.tile; tx < clip.right; tx += FOG_GRAIN.tile) {
          for (const [gx, gy] of grainPoints) {
            const px = tx + gx, py = ty + gy;
            if (px < clip.left || px > clip.right || py < clip.top || py > clip.bottom) continue;
            const alpha = lift * fogDensity(py, c, texture(fogImage, px, py, softScale));
            if (!alpha) continue;
            x.fillStyle = colour(tokens.light, alpha); x.beginPath(); x.arc(px, py, FOG_GRAIN.radius, 0, TAU); x.fill();
          }
        }
      }
      const seen = new Set();
      for (const b of rasterDrops) {
        const by = b.y;
        if (b.r < SPRITE_RADIUS_STEP) continue; // not formed yet: nothing to rasterize (and no empty tile)
        if (b.x + b.r < clip.left || b.x - b.r > clip.right || by + b.r < clip.top || by - b.r > clip.bottom) continue;
        const left = Math.floor((b.x - b.r) * scale), top = Math.floor((by - b.r) * scale);
        const right = Math.ceil((b.x + b.r) * scale), bottom = Math.ceil((by + b.r) * scale);
        const w = right - left, h = bottom - top;
        const sizeKey = `${w},${h}`;
        let tile = lensTiles.get(sizeKey);
        if (!tile) {
          const cv = canvas(); cv.width = w; cv.height = h;
          const ctx = cv.getContext('2d'); tile = { cv, ctx, mask: ctx.createImageData(w, h), image: ctx.createImageData(w, h) }; lensTiles.set(sizeKey, tile);
        }
        for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
          const px = (left + i + .5) / scale, py = (top + j + .5) / scale, p = (j * w + i) * 4;
          const pixel = (top + j) * output.width + left + i;
          // Neighboring bead tiles can overlap; composite each normal-map pixel once, as the shader does.
          if (left + i < 0 || left + i >= output.width || top + j < 0 || top + j >= output.height || seen.has(pixel)) {
            tile.mask.data[p + 3] = tile.image.data[p + 3] = 0; continue;
          }
          seen.add(pixel);
          const lens = lensLayer(px, py, c, texture(fogImage, px, py, softScale), texture(beadPixels(), px, py, scale));
          tile.mask.data[p + 3] = lens.clear * 255;
          for (let k = 0; k < 3; k++) tile.image.data[p + k] = lens.rgba[3] ? lens.rgba[k] / lens.rgba[3] : 0;
          tile.image.data[p + 3] = lens.rgba[3] * 255;
        }
        tile.ctx.putImageData(tile.mask, 0, 0); x.globalCompositeOperation = 'destination-out';
        x.drawImage(tile.cv, left / scale, top / scale, w / scale, h / scale);
        tile.ctx.putImageData(tile.image, 0, 0); x.globalCompositeOperation = 'source-over';
        x.drawImage(tile.cv, left / scale, top / scale, w / scale, h / scale);
      }
      x.restore();
    }
    function paintPanes() {
      let changed = outputDirty;
      for (const c of cards) {
        if (!c.dirty || !c.ly) continue;
        changed = true;
        if (backend) {
          drawGpu(c); c.ly.x.clearRect(0, 0, c.ly.cv.width, c.ly.cv.height);
          c.ly.x.drawImage(output, c.clip.left * scale, c.clip.top * scale, c.clip.width * scale, c.clip.height * scale, 0, 0, c.ly.cv.width, c.ly.cv.height);
          outputDirty = true; // Card shading used the visible buffer as scratch, even on layout-only frames.
        } else draw2d(c.ly.cv, c);
        c.dirty = false;
      }
      if (outputDirty) { if (backend) drawGpu(null); else draw2d(output, null); outputDirty = false; }
      return changed;
    }
    function render(backdropChanged, materialsChanged) {
      if (tokensDirty || sceneDirty) damage();
      if (tokensDirty) readTokens();
      if (layoutDirty) readLayout();
      if (backdropChanged || materialsChanged || materialDirty) {
        for (const c of cards) {
          if (!c.ly) continue;
          const before = c.material, material = api.glassFor(c.el);
          if (before?.alpha !== material?.alpha || before?.backdropBrightness !== material?.backdropBrightness ||
              material?.rgb.some((v, i) => v !== before?.rgb[i])) c.dirty = true;
          c.material = material;
        }
        materialDirty = false;
      }
      if (sourcesDirty) {
        sceneSources = [...api.back.querySelectorAll(BACKDROP_CANVASES)].map(c => ({ c, opacity: Number(getComputedStyle(c).opacity) }));
        sourcesDirty = false;
      }
      // Settled glass keeps its composite while weather moves. Only wet/material/geometry
      // damage samples the latest rear scene; weather alone must not repaint every pane.
      if (!outputDirty && !cards.some(c => c.dirty)) return false;
      copyScene();
      return paintPanes();
    }
    function tick(now) {
      frame = 0; if (!active || paused || destroyed) return;
      if (layoutDirty) readLayout();
      const oldAmount = amount, dt = Math.min(RUN_FRAME_MAX_MS, now - (lastTick || now)); lastTick = now;
      const wasPlaying = mode !== 'settled' || cardWet > 0;
      const running = runners.some(rn => !rn.done);
      if (running) stepRunners(now - passAt, dt, now);
      if (mode === 'pass') {
        const t = now - passAt;
        amount = smooth(t / FOG_GROW_MS); hand(Math.min(t, WIPE_AT_MS + WIPE_DURATION_MS), now);
        if (t >= FOG_THIN_AT_MS) { mode = 'thin'; amount = 1; fogDirty = true; }
      } else if (mode === 'thin') {
        // Even thinning toward the settled mist; the word keeps its ■ until the pass has settled.
        const k = clamp((now - passAt - FOG_THIN_AT_MS) / FOG_THIN_MS); amount = 1 - smooth(k);
        if (k === 1) { mode = 'settled'; amount = 0; beadsDirty = true; }
      } else if (mode === 'stop') {
        const k = clamp((now - stopAt) / SKIP_FADE_MS); amount = stopFrom * (1 - smooth(k));
        if (k === 1) { mode = 'settled'; amount = 0; resetWipe(); }
      }
      if (cardWet > 0 && now >= dryAt) { const k = clamp((now - dryAt) / dryMs); setCardWet(k === 1 ? 0 : dryFrom * (1 - smooth(k))); }
      // The word keeps its ■ until the cards are dry: until then a tap anywhere dries them at once.
      if (wasPlaying && mode === 'settled' && !cardWet) notify(null);
      if (oldAmount !== amount) { fogDirty = true; damage(); }
      if (burstDrops.length && now - burstAt >= BURST_MS) { burstDrops = []; beadsDirty = true; }
      if (fogDirty || regrowing || (fogBusy && now >= nextRegrowAt)) updateFog(now);
      drawFront(now);
      updateDisplaced(now);
      if (forming && now - formedAt >= BEAD_GROW_STEP_MS) beadsDirty = true;
      if (runners.some(rn => !rn.done && now - passAt >= rn.at - RUN_GROW_MS) || runners.some(rn => rn.done && !rn.gone)) beadsDirty = true; // a growing or running drop, or one just gone
      for (const rn of runners) if (rn.done) rn.gone = true;
      if (beadsDirty || burstDrops.length) drawBeads(now);
      const changed = tokensDirty || materialDirty || sourcesDirty || sceneDirty || outputDirty || cards.some(c => c.dirty);
      // The factory harness owns its frame; production composites after weather and outdoor, before ink.
      if (!api.requestWetFrame) refreshComposite();
      // While the pass moves, the sky's ink is re-measured at most every INK_PASS_MS; a settled change goes at once.
      const moving = mode === 'pass' || mode === 'thin' || mode === 'stop' || running; // running drops re-measure at 10 Hz too
      if (changed) inkPending = true;
      if (inkPending && (!moving || mode !== inkMode || now - inkAt >= INK_PASS_MS)) { invalidate(); inkPending = false; inkAt = now; inkMode = mode; }
      if (mode === 'pass' || mode === 'thin' || mode === 'stop' || running || (!frozen && regrowing) || burstDrops.length || (cardWet > 0 && now >= dryAt)) request();
      else {
        const wakeAt = Math.min(!frozen && fogBusy ? nextRegrowAt : Infinity, cardWet > 0 ? dryAt : Infinity);
        if (Number.isFinite(wakeAt)) wake = setTimeout(() => { wake = 0; request(); }, Math.max(0, wakeAt - now));
      }
    }
    // A layout move or a scroll clears the swaths (they belong to where the words were); drops already running keep running on the glass.
    function resetWipe(keepRunners = false) {
      cleared?.fill(0); holdUntil?.fill(0); trailMark.fill(0); fogBusy = regrowing = false; nextRegrowAt = Infinity; lastHand = null; burstDrops = [];
      if (!keepRunners) runners = [];
      for (const b of drops) { delete b.pushedY; delete b.pushedUntil; delete b.pushedAlpha; }
      fogDirty = beadsDirty = true;
    }
    function settled(still = false) {
      cancelPointer(); cancel(); mode = 'settled'; amount = 0; frozen = still || reduced(); resetWipe(); hideFront(); setCardWet(0);
      fogDirty = true; beadsDirty = true; notify(null); request();
    }
    function clear() {
      cancelPointer(); active = false; setCardWet(0);
      cancel(); mode = 'settled'; amount = 0; resetWipe(); hideFront();
      for (const el of layers.keys()) removeLayer(el);
      if (output) output.hidden = true;
      if (frontC) frontC.hidden = true;
      notify(null); invalidate();
    }
    function setup() {
      output = canvas();
      backend = force2D ? null : gpu(output);
      if (!force2D && !backend) output = canvas(); // A canvas that tried WebGL cannot subsequently acquire a 2D context.
      output.className = 'sky-wet-viewport'; output.setAttribute('aria-hidden', 'true'); output.hidden = !active;
      api.back.appendChild(output); output.addEventListener('webglcontextlost', contextLost);
      if (api.front) {
        // Below the front lightning, as the outdoor air's front layers are.
        frontC = canvas(); frontC.className = 'sky-wet-front'; frontC.setAttribute('aria-hidden', 'true'); frontC.hidden = !active;
        api.front.insertBefore(frontC, api.front.querySelector('#front-flash')); frontX = frontC.getContext('2d');
        frontMaskC = canvas(); frontMaskX = frontMaskC.getContext('2d');
      }
      fogC = canvas(); viewC = canvas(); beadC = canvas();
      // The scene and bead maps are read back on every changed frame (ink samples, the 2D path): keep them CPU-side.
      const readable = { willReadFrequently: true };
      fogX = fogC.getContext('2d'); viewX = viewC.getContext('2d', readable); beadX = beadC.getContext('2d', readable);
      grainImage = makeGrain();
    }
    function resize() {
      if (destroyed) return;
      if (paused) { resizeDue = true; return; }
      if (!output) setup();
      resizeDue = false; cancelPointer();
      const size = api.size(); W = Math.max(1, size.W); H = Math.max(1, size.H);
      const dimension = backend?.dimension || BUFFER_DIMENSION_MAX;
      scale = Math.min(W < PHONE_WIDTH_PX ? Math.max(CRISP_BUFFER_SCALE, size.dpr || 1) : CRISP_BUFFER_SCALE, Math.sqrt(VIEWPORT_PIXEL_MAX / (W * H)), dimension / W, dimension / H);
      softScale = Math.min(SOFT_BUFFER_SCALE[W < PHONE_WIDTH_PX ? 'phone' : 'wide'], Math.sqrt(SOFT_PIXEL_MAX / (W * H)), dimension / W, dimension / H);
      output.width = beadC.width = Math.max(1, Math.floor(W * scale)); output.height = beadC.height = Math.max(1, Math.floor(H * scale));
      fw = fogC.width = viewC.width = Math.max(1, Math.floor(W * softScale));
      fh = fogC.height = viewC.height = Math.max(1, Math.floor(H * softScale));
      fogImage = fogX.createImageData(fw, fh); fallbackImage = fogX.createImageData(fw, fh);
      cleared = new Float32Array(fw * fh); trailMark = new Uint8Array(fw * fh); holdUntil = new Float64Array(fw * fh); regrowFor = new Float32Array(fw * fh).fill(REGROW_MS); base = new Float32Array(fw * fh);
      for (let j = 0; j < fh; j++) for (let i = 0; i < fw; i++) base[j * fw + i] = clamp(FOG_PATTERN.body + FOG_PATTERN.wave * Math.sin(i * FOG_PATTERN.x) * Math.sin(j * FOG_PATTERN.y) + FOG_PATTERN.lower * j / fh);
      // Per texel: its distance from the breath's source (1 at the top corners), and how deep it sits from the frame
      // (a rounded rectangle with the screen's own shape: 0 at the corners, 1 at the centre), its edge made lumpy.
      bloomD = new Float32Array(fw * fh); edgeE = new Float32Array(fw * fh);
      const gw = Math.ceil(W / CLEAR_LUMPS.cell) + 2, noise = Float32Array.from({ length: gw * (Math.ceil(H / CLEAR_LUMPS.cell) + 2) }, () => Math.random() * 2 - 1);
      const lumpAt = (x, y) => {
        const gx = x / CLEAR_LUMPS.cell, gy = y / CLEAR_LUMPS.cell, i0 = Math.floor(gx), j0 = Math.floor(gy), sx = smooth(gx - i0), sy = smooth(gy - j0), n = (i, j) => noise[j * gw + i];
        return (n(i0, j0) * (1 - sx) + n(i0 + 1, j0) * sx) * (1 - sy) + (n(i0, j0 + 1) * (1 - sx) + n(i0 + 1, j0 + 1) * sx) * sy;
      };
      for (let j = 0; j < fh; j++) for (let i = 0; i < fw; i++) {
        const x = (i + .5) / softScale, y = (j + .5) / softScale, u = x / W * 2 - 1, v = y / H * 2 - 1, o = j * fw + i;
        bloomD[o] = Math.hypot(u, BLOOM.y - y / H) / Math.hypot(1, BLOOM.y);
        edgeE[o] = clamp(1 - (u ** 4 + v ** 4) ** .25 / 2 ** .25 + CLEAR_LUMPS.amplitude * lumpAt(x, y));
      }
      if (frontC) {
        frontScale = Math.min(size.dpr || 1, Math.sqrt(FRONT_PIXEL_MAX / (W * H)), dimension / W, dimension / H);
        frontC.width = Math.max(1, Math.floor(W * frontScale)); frontC.height = Math.max(1, Math.floor(H * frontScale)); frontMaskC.width = fw; frontMaskC.height = fh; frontImage = frontMaskX.createImageData(fw, fh); frontShown = false; }
      readTokens(); makeDrops(); layoutDirty = sceneDirty = fogDirty = sourcesDirty = true; lastHand = null; fogBusy = regrowing = false; nextRegrowAt = Infinity; request();
    }
    function redraw() { if (destroyed) return; tokensDirty = sceneDirty = fogDirty = sourcesDirty = materialDirty = true; request(); }
    function layout() { layoutDirty = true; request(); }
    function scroll() { cancelPointer(); resetWipe(true); layout(); }
    function dragStart(e) { if (pointerState && !e.target?.closest?.(CONTROL_SELECTOR)) e.preventDefault(); }
    // Chart scrubbing only changes transient readouts/markers, not the pane geometry.
    const observer = new MutationObserver(records => {
      for (const r of records) {
        const changes = r.type === 'childList' ? [...r.addedNodes, ...r.removedNodes] : [];
        if (r.target === api.back) {
          if (changes.some(n => n.nodeType === 1 && n.matches(BACKDROP_CANVASES))) { sourcesDirty = sceneDirty = true; request(); }
          continue;
        }
        const el = r.target.nodeType === 1 ? r.target : r.target.parentElement;
        if (!el || el.closest(TRANSIENT)) continue;
        if (r.type !== 'childList' || changes.some(n => n.nodeType !== 1 || !n.matches(TRANSIENT))) layout();
      }
    });
    observer.observe(document.querySelector('.page') || document.body,
      { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'hidden'] });
    observer.observe(api.back, { childList: true });
    const sizes = new ResizeObserver(layout);
    const theme = new MutationObserver(redraw); theme.observe(html, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('scroll', scroll, { passive: true });
    // The shared controller owns production resizing; standalone factories own theirs.
    if (!api.requestWetFrame) window.addEventListener('resize', resize);
    window.addEventListener('blur', cancelPointer); window.addEventListener('dragstart', dragStart);
    function contextLost(e) {
      e.preventDefault(); if (destroyed || !backend) return;
      backend.dispose(); backend = null;
      const old = output; output = canvas(); output.className = old.className; output.width = old.width; output.height = old.height; output.hidden = old.hidden;
      output.setAttribute('aria-hidden', 'true');
      old.replaceWith(output); old.removeEventListener('webglcontextlost', contextLost); damage(); request();
    }
    function texture(im, x, y, resolution) {
      if (!im) return [0, 0, 0, 0];
      const u = clamp(x * resolution - .5, 0, im.width - 1), v = clamp(y * resolution - .5, 0, im.height - 1);
      const x0 = Math.floor(u), y0 = Math.floor(v), x1 = Math.min(x0 + 1, im.width - 1), y1 = Math.min(y0 + 1, im.height - 1);
      const read = (i, j) => Array.from(im.data.subarray((j * im.width + i) * 4, (j * im.width + i) * 4 + 4));
      return mix(mix(read(x0, y0), read(x1, y0), u - x0), mix(read(x0, y1), read(x1, y1), u - x0), v - y0);
    }
    function refreshComposite(backdropChanged = false, materialsChanged = false) {
      if (active && !paused && !destroyed) render(backdropChanged, materialsChanged);
    }
    resize();
    return {
      setConditions(conditions) {
        if (destroyed) return;
        if (!conditions.some(c => c.kind === 'damp')) { clear(); return; }
        if (!active) {
          active = true; mode = 'settled'; frozen = reduced(); // settled at once: dry cards, the condensation behind them
          if (output) { output.hidden = false; makeDrops(); }
          if (frontC) frontC.hidden = false;
          layoutDirty = sceneDirty = fogDirty = beadsDirty = true; request();
        }
        else redraw();
      },
      play(kind) {
        if (destroyed || !active || paused || kind !== 'damp') return;
        if (reduced()) { settled(true); return; }
        cancelPointer(); cancel(); mode = 'pass'; frozen = false; passAt = performance.now(); amount = 0; lastHand = null;
        setCardWet(1); dryAt = passAt + CARD_DRY_AT_MS; dryMs = CARD_DRY_MS; dryFrom = 1;
        cleared.fill(0); holdUntil.fill(0); burstDrops = []; readLayout(); makeDrops(true); makeRunners(); lastTick = 0; fogDirty = sceneDirty = true; notify('damp'); request();
      },
      settle() { settled(); }, still() { settled(true); },
      // A skip: the pass fades out quickly, and the cards dry with it (also while they are still wet after the pass).
      stop() {
        if (paused || destroyed) return;
        const now = performance.now();
        if (['pass', 'thin'].includes(mode)) { mode = 'stop'; stopAt = now; stopFrom = amount; lastHand = null; runners = []; beadsDirty = true; }
        else if (!cardWet) return;
        dry(now, SKIP_FADE_MS); request();
      },
      burst(kinds) {
        if (!active || paused || frozen || reduced() || mode !== 'settled' || !kinds.includes('damp')) return;
        burstAt = performance.now(); burstDrops = Array.from({ length: BURST_DROP_MAX }, () => ({ x: W * (BURST_START.left + Math.random() * BURST_START.width), y: H * (BURST_START.top + Math.random() * BURST_START.height), r: BURST_START.radius, burst: true })); request();
      },
      setPaused(hidden) { cancelPointer(); paused = hidden; if (hidden) { cancel(); mode = 'settled'; amount = 0; resetWipe(); hideFront(); setCardWet(0); notify(null); } else { if (resizeDue) resize(); redraw(); } },
      clear, resize, redraw, pointer,
      // Call once after weather → outdoor drawing, before ink. No replay, RAF scheduling or recursive invalidation.
      refreshComposite,
      // Same field/shader order as the visible sky: called weather → outdoor → wet by the shared ink solver.
      sampleBackground(rgb, x, y) {
        if (!active || !fogImage || output.hidden) return rgb;
        const f = texture(fogImage, x, y, softScale), b = texture(beadPixels(), x, y, scale);
        const fog = fogLayer(x, y, null, f), lens = lensLayer(x, y, null, f, b);
        const keep = (1 - lens.clear) * (1 - lens.rgba[3]);
        const alpha = lens.rgba[3] + fog[3] * keep;
        return rgb.map((v, k) => lens.rgba[k] + fog[k] * keep + v * (1 - alpha));
      },
      destroy() {
        if (destroyed) return; clear(); destroyed = true; observer.disconnect(); sizes.disconnect(); theme.disconnect();
        window.removeEventListener('scroll', scroll);
        if (!api.requestWetFrame) window.removeEventListener('resize', resize);
        window.removeEventListener('blur', cancelPointer); window.removeEventListener('dragstart', dragStart);
        if (output) {
          output.removeEventListener('webglcontextlost', contextLost); backend?.dispose(); backend = null; output.remove();
          if (frontC) { frontC.remove(); frontC.width = frontC.height = frontMaskC.width = frontMaskC.height = 1; frontC = null; }
          for (const c of [output, fogC, beadC, viewC, ...sprites.values()]) c.width = c.height = 1;
        }
        for (const { cv } of lensTiles.values()) cv.width = cv.height = 1;
        lensTiles.clear(); sprites.clear(); observed.clear(); drops = []; cards = []; sceneSources = []; fogImage = fallbackImage = beadImage = viewImage = cleared = holdUntil = regrowFor = base = null;
      },
    };
  }
  return { create };
})();
