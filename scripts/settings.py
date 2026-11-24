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
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # the app's config.py holds the backup helpers
from config import backup_config, backups, copy_now, unique  # noqa: E402

COORDINATES = ("lat", "lon")  # [place] keys `show` with no section leaves out; `show place` prints them


def fail(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def cmd_backup(cfg: Path, _):
    if not cfg.is_file():
        fail(f"No config.toml in {cfg.parent}. Run setup first (SETUP.md) or check the folder.")
    print(backup_config(cfg))


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
