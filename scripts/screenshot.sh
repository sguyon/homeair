#!/usr/bin/env bash
# The README's pictures, all from the demo (made-up data, never real readings):
#   docs/hero-light.png, docs/hero-dark.png  the desktop page (Morning rush) in a rounded panel and a phone (All clear)
#                                             over its lower right corner, each on the page's first screen, transparent
#                                             background (≤ 1600 px wide)
#   docs/screenshot.png                       the whole desktop page, light theme (1440 px wide)
#
#   scripts/screenshot.sh                                   starts the demo on port 5556, stops it after
#   URL=http://127.0.0.1:5556/ scripts/screenshot.sh        uses a demo that is already running
#   PY=/path/to/python scripts/screenshot.sh               the Python that runs the demo
#   CROPPED_OUT=/path/hero.jpg scripts/screenshot.sh       only the cropped hero: the light hero on a white background,
#                                                           cropped tight with lighter shadows, for posts and link previews:
#                                                           a 1446 × 862 JPG (nothing in docs/ changes)
#
# Needs Google Chrome, Node.js and ImageMagick (magick). Chrome runs headless with its software renderer,
# so the WebGL map draws. The page's ?shot mode gives the light theme (?shot=dark the dark one) without the
# sky, the dev line or the demo's top area, without touching any saved settings. The desktop panel and the full page show
# the "rush" moment, the phone "clear". scripts/hero.html draws the composition.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
# for the demo server: $PY, else the first of .venv's Python and python3 that has the app's requirements
if [ -z "${PY:-}" ]; then
  for p in "$ROOT/.venv/bin/python" "$(command -v python3 || true)"; do
    [ -x "$p" ] && "$p" -c 'import flask' 2>/dev/null && { PY="$p"; break; }
  done
fi

[ -x "$CHROME" ] || { echo "Chrome not found at $CHROME (set CHROME=...)"; exit 1; }
command -v magick >/dev/null || { echo "ImageMagick (magick) is needed: brew install imagemagick"; exit 1; }
command -v node >/dev/null || { echo "Node.js is needed (it drives Chrome)"; exit 1; }

TMP="$(mktemp -d)"; SERVER=""
cleanup() { [ -n "$SERVER" ] && kill "$SERVER" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

if [ -z "${URL:-}" ]; then
  [ -n "${PY:-}" ] || { echo "No Python with flask found: install requirements.txt, or set PY=/path/to/python"; exit 1; }
  URL="http://127.0.0.1:5556/"
  # never the real page: the demo serves made-up data and needs no config or token
  (cd "$ROOT" && DEMO=1 exec "$PY" -m flask --app server run --port 5556) >"$TMP/server.log" 2>&1 &
  SERVER=$!
  for _ in $(seq 1 40); do curl -sf -o /dev/null --max-time 2 "$URL" && break; sleep 0.5; done
fi
curl -sf -o /dev/null --max-time 10 "$URL" || { echo "The demo isn't answering at $URL."; [ -f "$TMP/server.log" ] && tail -5 "$TMP/server.log"; exit 1; }
# a local dev server adds its own tools to the page (a script under /_dev/): they must never reach the README's pictures
if curl -sf --max-time 10 "$URL" | grep -q '/_dev/'; then
  echo "$URL adds local dev tools to the page. Use a plain demo: run this script without URL, or start DEMO=1 flask on a free port."; exit 1
fi
case "$URL" in *\?*) Q="&";; *) Q="?";; esac
PAGE="${URL}${Q}moment=rush&shot"     # the desktop panel and the full page: the morning rush, with things to do
CLEAR="${URL}${Q}moment=clear&shot"   # the phone: all clear

DESK_VIEW=1280x800   # the desktop capture: 16:10, narrower than 1440 so the Summary's text reads larger in the panel
PHONE_VIEW=393x793   # the phone capture: a 393 × 852 pt iPhone's screen below its 59 pt status bar (hero.html draws the bar)
HERO_VIEW=1600x1300  # hero.html's window: its stage (about 1590 × 1230 CSS px) fits inside
HERO_DPR=2           # hero.html is shot at twice its size, then scaled down to ≤ 1600 px wide, so text stays sharp
HERO_MAX_W=1600      # the README images' width: crisp at the README's 800 px on a 2x screen, still a small file
HERO_BORDER=6        # px of nothing kept around the trimmed shot (about 3 px after scaling: the edge check needs 2)
CROPPED_W=1446; CROPPED_H=862                        # the cropped hero, about 5:3: the composition with almost nothing around it
CROPPED_SIDE=24; CROPPED_TOP=17; CROPPED_BOTTOM=30   # its least margins in px: devices nearly edge to edge, room below for the shadow to fade
CROPPED_SOLID=50%    # alpha above which a pixel counts as the devices, not their shadow, when fitting the margins
CROPPED_QUALITY=88   # JPG quality: no visible artefacts on text, a much smaller file than a PNG

shoot() { node "$ROOT/scripts/screenshot.mjs" "$CHROME" "$1" "$2" "$TMP/profile-$RANDOM" "${@:3}" >/dev/null; }   # one Chrome at a time

# hero <theme> <out.png> [look]: the two captures composed by hero.html, shot on transparent, trimmed to the last pixel
# with any alpha at all (the shadows fade out inside hero.html's room), then HERO_BORDER px of nothing
hero() {
  local theme="$1" out="$2" look="${3:-}"
  shoot "$PAGE" "$TMP/desk-$theme.png" --viewport "$DESK_VIEW" --dpr 2 --first-screen --theme "$theme"
  shoot "$CLEAR" "$TMP/phone-$theme.png" --viewport "$PHONE_VIEW" --dpr 3 --first-screen --theme "$theme"
  local bg="#$(magick "$TMP/phone-$theme.png" -format '%[hex:p{6,6}]' info: | cut -c1-6)"   # the page's background, behind the phone's status bar
  local url="file://$ROOT/scripts/hero.html?theme=$theme&look=$look&bg=${bg/\#/%23}&desk=file://$TMP/desk-$theme.png&phone=file://$TMP/phone-$theme.png"
  shoot "$url" "$TMP/hero-raw.png" --viewport "$HERO_VIEW" --dpr "$HERO_DPR" --first-screen --transparent
  local box="$(magick "$TMP/hero-raw.png" -alpha extract -threshold 0 -format '%@' info:)"
  magick "$TMP/hero-raw.png" -crop "$box" +repage -bordercolor none -border "$HERO_BORDER" "$out"
}

if [ -n "${CROPPED_OUT:-}" ]; then
  hero light "$TMP/cropped-cut.png" cropped
  # fit the devices (not their shadow's tail) inside the margins, centred, and let the shadow run on over the white
  read -r vw vh vx vy < <(magick "$TMP/cropped-cut.png" -alpha extract -threshold "$CROPPED_SOLID" -format '%@\n' info: | sed -E 's/([0-9]+)x([0-9]+)\+([0-9]+)\+([0-9]+)/\1 \2 \3 \4/')
  read -r cw ch < <(magick identify -format '%w %h\n' "$TMP/cropped-cut.png")
  read -r nw nh ox oy < <(awk -v vw="$vw" -v vh="$vh" -v vx="$vx" -v vy="$vy" -v cw="$cw" -v ch="$ch" -v W="$CROPPED_W" -v H="$CROPPED_H" \
    -v side="$CROPPED_SIDE" -v top="$CROPPED_TOP" -v bottom="$CROPPED_BOTTOM" 'BEGIN {
      s = (W - 2 * side) / vw; t = (H - top - bottom) / vh; if (t < s) s = t
      printf "%d %d %d %d\n", cw * s + .5, ch * s + .5, (W - vw * s) / 2 - vx * s + .5, top + (H - top - bottom - vh * s) / 2 - vy * s + .5 }')
  magick -size "${CROPPED_W}x${CROPPED_H}" xc:white \( "$TMP/cropped-cut.png" -filter Lanczos -resize "${nw}x${nh}!" \) \
    -geometry "+${ox}+${oy}" -composite -alpha off -strip -quality "$CROPPED_QUALITY" "$CROPPED_OUT"
  echo "Saved $CROPPED_OUT ($(magick identify -format '%wx%h' "$CROPPED_OUT"))"
  exit 0
fi

for theme in light dark; do
  hero "$theme" "$TMP/hero-$theme-cut.png"
  magick "$TMP/hero-$theme-cut.png" -filter Lanczos -resize "${HERO_MAX_W}x>" -strip -define png:compression-level=9 "$ROOT/docs/hero-$theme.png"
  # every pixel of the outer 2 px must be fully transparent, or GitHub shows the shadow's cut edge
  edge="$(magick "$ROOT/docs/hero-$theme.png" -alpha extract -fill black -draw "rectangle 2,2 %[fx:w-3],%[fx:h-3]" -alpha off -format '%[max]' info:)"
  [ "$edge" = "0" ] || { echo "docs/hero-$theme.png: the edge is not fully transparent (max alpha $edge)"; exit 1; }
  echo "Saved docs/hero-$theme.png ($(magick identify -format '%wx%h' "$ROOT/docs/hero-$theme.png"))"
done

shoot "$PAGE" "$TMP/page.png" --viewport 1440x900 --dpr 1 --theme light
magick "$TMP/page.png" -strip -define png:compression-level=9 "$ROOT/docs/screenshot.png"
echo "Saved docs/screenshot.png ($(magick identify -format '%wx%h' "$ROOT/docs/screenshot.png"))"
