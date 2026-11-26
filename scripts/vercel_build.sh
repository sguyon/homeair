#!/usr/bin/env bash
# Vercel build for the demo. Vercel serves public/** from its CDN; Flask's own
# static folder is not used there. static/ stays in place because server.py
# reads it at runtime to hash file names for the ?v= URLs.
# demo/road-data/ stays with the Flask function's source (outside public/):
# the checked NYC clip serves /api/road/route without a runtime download.
# Root icons, /manifest.json and /sw.js stay Flask routes: they vary by environment.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf public
mkdir -p public
cp -R static public/static
