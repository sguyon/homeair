"""scripts/settings.py, the undo for "Change your home" (AGENTS.md): restore must bring back the newest backup even when
the config's modified time is old (after a restore, or an editor that keeps it), and the overview leaves out where the
home is."""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "settings.py"
PAST = 1_500_000_000   # 2017: a config whose modified time is older than its backups


def settings(folder, *args):
    out = subprocess.run([sys.executable, str(SCRIPT), "--dir", str(folder), *args], capture_output=True, text=True,
                         env={**os.environ, "HOME": str(folder)})
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def write(cfg, text):
    cfg.write_text(text)
    os.utime(cfg, (PAST, PAST))


def test_restore_brings_back_the_newest_backup_whatever_the_config_times(tmp_path):
    cfg = tmp_path / "config.toml"
    write(cfg, 'v = "1"\n')
    first = settings(tmp_path, "backup").strip()
    write(cfg, 'v = "2"\n')
    settings(tmp_path, "backup")
    write(cfg, 'v = "3"\n')
    settings(tmp_path, "restore")
    assert cfg.read_text() == 'v = "2"\n'
    settings(tmp_path, "restore", first)   # the exact path `backup` printed, as AGENTS.md says
    assert cfg.read_text() == 'v = "1"\n'
    assert len(list(tmp_path.glob("config.toml.pre-restore-*"))) == 2   # two restores in one second keep both files


def test_show_leaves_out_the_coordinates_unless_asked_for_the_place(tmp_path):
    (tmp_path / "config.toml").write_text('[place]\nname = "Greenpoint"\nlat = 40.73\nlon = -73.95\n\n[limits]\nco2 = 900\n')
    overview = settings(tmp_path, "show")
    assert json.loads(overview)["place"] == {"name": "Greenpoint"} and "40.73" not in overview
    assert json.loads(settings(tmp_path, "show", "place"))["place"]["lat"] == 40.73
