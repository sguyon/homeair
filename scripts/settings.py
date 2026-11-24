"""Safe parts of changing a Home Air config.toml: back it up, list backups, restore one, show current settings.
The agent edits the file itself; this script never edits it and never reads .env.

Used by an agent changing a home (AGENTS.md, "Change your home").

Usage: scripts/run.sh scripts/settings.py [--dir FOLDER] backup | restore [BACKUP] | list | show [SECTION]
`show` with no section leaves out the home's coordinates; `show place` prints them.
Folder: --dir, else HOME_AIR_DIR, else this Home Air folder. Backups sit next to config.toml as config.toml.bak-<UTC time>.
"""
import argparse
import json
import os
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

KEEP = 5  # newest backups kept; older ones are deleted by `backup`
COORDINATES = ("lat", "lon")  # [place] keys `show` with no section leaves out; `show place` prints them


def fail(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def backups(cfg: Path):
    """Backups of cfg, newest first, by when they were made: each copy gets the time it was made as its modified time
    (copy_now), here and in scripts/setup.py, which makes them with the same prefix."""
    found = [p for p in cfg.parent.glob(cfg.name + ".bak-*") if p.is_file()]
    return sorted(found, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def unique(cfg: Path, kind):
    """config.toml.<kind>-<UTC time>, with -2, -3… when one was already made in the same second."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest, n = cfg.with_name(f"{cfg.name}.{kind}-{stamp}"), 2
    while dest.exists():
        dest, n = cfg.with_name(f"{cfg.name}.{kind}-{stamp}-{n}"), n + 1
    return dest


def copy_now(src: Path, dest: Path):
    """Copy the contents and permissions, with now as the modified time: copy2 would keep the config's old time, and an
    older backup could then sort as the newest."""
    shutil.copyfile(src, dest)
    shutil.copymode(src, dest)
    os.utime(dest)


def cmd_backup(cfg: Path, _):
    if not cfg.is_file():
        fail(f"No config.toml in {cfg.parent}. Run setup first (SETUP.md) or check the folder.")
    dest = unique(cfg, "bak")
    copy_now(cfg, dest)
    for old in backups(cfg)[KEEP:]:
        old.unlink()
    print(dest)


def cmd_restore(cfg: Path, args):
    if args.backup:
        src = Path(args.backup)
        if not src.is_absolute() and not src.exists():
            src = cfg.parent / args.backup
        if src.parent.resolve() != cfg.parent.resolve() or not src.name.startswith(cfg.name + ".bak-"):
            fail(f"{args.backup} is not a backup of this config. Run `list` to see them.")
    else:
        found = backups(cfg)
        if not found:
            fail("No backups to restore. Nothing was changed.")
        src = found[0]
    if not src.is_file():
        fail(f"{src} does not exist. Run `list` to see the backups.")
    try:
        tomllib.loads(src.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        fail(f"{src.name} is not readable TOML ({e}). Nothing was changed.")
    kept = None
    if cfg.is_file():   # the file being replaced is kept too, so a restore can itself be undone (not counted as a backup)
        kept = unique(cfg, "pre-restore")
        copy_now(cfg, kept)
    tmp = cfg.with_name(cfg.name + ".restoring")
    copy_now(src, tmp)
    os.replace(tmp, cfg)  # whole-file swap: never half-written
    print(f"Restored {src.name} to {cfg}." + (f" The replaced file is kept as {kept.name}." if kept else "")
          + " Restart the app to apply it: scripts/run.sh scripts/setup.py --start")


def cmd_list(cfg: Path, _):
    found = backups(cfg)
    if not found:
        print("No backups.")
    for p in found:
        print(p)


def cmd_show(cfg: Path, args):
    if not cfg.is_file():
        fail(f"No config.toml in {cfg.parent}.")
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        fail(f"config.toml is not valid TOML: {e}")
    if args.section:
        if args.section not in data:
            fail(f"No [{args.section}] in config.toml. Sections present: {', '.join(data)}")
        data = {args.section: data[args.section]}
    elif isinstance(data.get("place"), dict):   # the overview leaves the home's coordinates out, as the MCP answers do
        data["place"] = {k: v for k, v in data["place"].items() if k not in COORDINATES}
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="the Home Air folder (default: HOME_AIR_DIR, else this one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup")
    r = sub.add_parser("restore")
    r.add_argument("backup", nargs="?")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("section", nargs="?")
    args = ap.parse_args()
    folder = Path(args.dir or os.environ.get("HOME_AIR_DIR") or Path(__file__).resolve().parents[1]).expanduser()
    if not folder.is_dir():
        fail(f"{folder} is not a folder. Set HOME_AIR_DIR to the Home Air folder.")
    {"backup": cmd_backup, "restore": cmd_restore, "list": cmd_list, "show": cmd_show}[args.cmd](folder / "config.toml", args)


if __name__ == "__main__":
    main()
