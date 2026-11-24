"""`setup.py --home-server` (where-it-runs spec, "A home server"): setup stays on this computer, where the token page
works; this prepares a folder with what a home server needs to run the app with Docker (the Compose file using the
published image, the settings with the monitors' addresses found now, and the keys saved in .env) and says the two steps
to run there. A container can't browse the Wi-Fi, so the addresses are written here. The agent doesn't copy it over SSH."""
import os
import shutil
from pathlib import Path

IMAGE = "ghcr.io/sguyon/homeair:latest"           # published from main by .github/workflows/image.yml
OLD_IMAGE = "ghcr.io/sguyon/home-air-quality:latest"   # the same image under the old name, still published
FOLDER = "home-server"                             # prepared in the repo folder; gitignored (it holds a copy of .env)
NO_TOKEN_LINE = ("Without the AirGradient token, the server stops showing live readings if a monitor's address changes: "
                 "run this step again then (adding the token avoids it).")   # spec answer A
SERVER_PORT = 8000                                 # the image's port (Dockerfile), opened to the home network

COMPOSE = f"""# Home Air on a home server. In this folder: docker compose up -d
# Then open http://<this server>:{SERVER_PORT} from any device at home. Anyone on the home network can see the page.
# Prepared by scripts/setup.py --home-server: config.toml and .env sit next to this file.
# Keep .env private; config.toml must stay readable by the container's user.
services:
  home-air-quality:
    image: {IMAGE}   # if this can't be pulled, the same image is {OLD_IMAGE}
    ports:
      - "{SERVER_PORT}:{SERVER_PORT}"
    volumes:
      - type: bind
        source: ./config.toml
        target: /config/config.toml
        read_only: true
        bind:
          create_host_path: false
      - homeair-history:/data   # survives `docker compose up -d --force-recreate` and a new image; `docker compose down -v` deletes it
    env_file: .env
    environment:
      HISTORY_FILE: /data/history.sqlite   # the readings' history and the remembered addresses live in the volume below
    restart: unless-stopped

volumes:
  homeair-history:
"""


def compose_text():
    return COMPOSE


def with_hosts(text, cfg, find):
    """config.toml's text with `<kind>_host` set for each configured serial found on the Wi-Fi now and removed for the
    others (a host from an earlier run must not stay when its monitor isn't found). Returns (text, lines to print)."""
    import config
    import localnet
    from setup import NO_MONITOR
    mons = config.monitors(cfg)
    if not mons:
        return text, []
    found = {f.serial: f for f in find(serials=[m.serial for m in mons])}
    blocker = localnet.environment_blocker()
    values, lines = {}, []
    for mon in mons:
        item = found.get(mon.serial)
        if item is None:
            values[mon.kind] = {f"{mon.kind}_host": None}
            why = NO_MONITOR[blocker] if blocker else "no answer on this network"
            lines.append(f"{mon.kind}: not found ({why}): no address written, so the server reads it from the cloud (needs the token)")
            continue
        values[mon.kind] = {f"{mon.kind}_host": item.address if item.port == 80 else f"{item.address}:{item.port}"}
        lines.append(f"{mon.kind}: found on your Wi-Fi, address written for the server")
    return config.set_monitor_keys(text, values), lines


def prepare(root, find=None):
    import localnet
    from config import ENV_FILE_MODE, ConfigError, env_key, load_config
    from setup import HOME_CMD, saved_keys
    find = find or localnet.discover
    try:
        cfg = load_config({}, root)
    except ConfigError as e:
        print(f"config.toml has a problem ({e}). Fix it first: {HOME_CMD}")
        return 1
    if cfg is None:
        print(f"There's no config.toml yet. Set up the home first: {HOME_CMD}")
        return 1
    token = env_key("AIRGRADIENT_TOKEN", saved_keys(root))
    text, lines = with_hosts((root / "config.toml").read_text(encoding="utf-8"), cfg, find)
    folder = root / FOLDER
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(compose_text())
    (folder / "config.toml").write_text(text, encoding="utf-8")
    env = folder / ".env"
    source = root / ".env"
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, ENV_FILE_MODE)
    with os.fdopen(fd, "wb") as out:   # a copy of .env; an empty file when there is none (the Compose file names it)
        out.write(source.read_bytes() if source.exists() else b"")
    env.chmod(ENV_FILE_MODE)
    print(f"Prepared {folder} for a home server with Docker.")
    for line in lines:
        print(line)
    if not token:
        print(NO_TOKEN_LINE)
    print(f"1. Copy the {FOLDER} folder to your server, keeping .env private, for example: scp -rp {FOLDER} you@your-server:")
    print(f"2. On the server, in the {FOLDER} folder: docker compose up -d")
    print(f"Then open http://your-server:{SERVER_PORT} from any device at home.")
    return 0
