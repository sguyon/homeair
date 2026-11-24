"""./homeair: each short command runs the right script with the right flags, through scripts/run.sh. A fake run.sh
records what it was asked; nothing real runs."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# what the person types -> what scripts/run.sh is asked to run, in order
TABLE = [
    (["setup"], ["scripts/setup.py"]),
    (["setup", "--address", "Times Square, New York", "--profiles", "sinus,runner"],
     ["scripts/setup.py --address Times Square, New York --profiles sinus,runner"]),
    (["start"], ["scripts/setup.py --start"]),
    (["serve"], ["server.py"]),
    (["stop"], ["scripts/setup.py --stop"]),
    (["status"], ["scripts/setup.py --status"]),
    (["check"], ["scripts/check_config.py", "scripts/setup.py --live"]),
    (["check", "http://server.local:8000"], ["scripts/check_config.py", "scripts/setup.py --live http://server.local:8000"]),
    (["check", "--config"], ["scripts/check_config.py"]),
    (["check", "--suggest"], ["scripts/check_config.py --suggest"]),
    (["connect"], ["scripts/setup.py --connect"]),
    (["connect", "--wait"], ["scripts/setup.py --wait-token"]),
    (["start-at-login", "on"], ["scripts/setup.py --start-at-login on"]),
    (["start-at-login", "off"], ["scripts/setup.py --start-at-login off"]),
    (["skill", "add"], ["scripts/setup.py --agent on"]),
    (["skill", "remove"], ["scripts/setup.py --agent off"]),
    (["skill", "status"], ["scripts/setup.py --agent status"]),
    (["monitors"], ["scripts/setup.py --monitors"]),
    (["monitors", "use", "84fce6111111"], ["scripts/setup.py --monitor 84fce6111111"]),
    (["settings", "show"], ["scripts/settings.py show"]),
    (["settings", "show", "place"], ["scripts/settings.py show place"]),
    (["settings", "backup"], ["scripts/settings.py backup"]),
    (["settings", "restore", "config.toml.bak-1"], ["scripts/settings.py restore config.toml.bak-1"]),
    (["settings", "list"], ["scripts/settings.py list"]),
    (["home-server"], ["scripts/setup.py --home-server"]),
    (["railway", "--service", "homeair"], ["scripts/setup.py railway --service homeair"]),
]


@pytest.fixture
def folder(tmp_path):
    """A Home Air folder with the real ./homeair and a run.sh that records its arguments (and fails check_config.py
    when FAIL_CHECK is set)."""
    shutil.copy(ROOT / "homeair", tmp_path / "homeair")
    (tmp_path / "scripts").mkdir()
    run = tmp_path / "scripts" / "run.sh"
    run.write_text('#!/bin/sh\necho "$*" >> "$LOG"\n'
                   'if [ -n "${FAIL_CHECK:-}" ] && [ "$1" = scripts/check_config.py ]; then exit 1; fi\n')
    run.chmod(0o755)
    return tmp_path


def homeair(folder, *args, **env):
    log = folder / "calls.log"
    log.unlink(missing_ok=True)
    r = subprocess.run(["/bin/sh", str(folder / "homeair"), *args], cwd="/", capture_output=True, text=True, timeout=10,
                       env={"PATH": os.environ["PATH"], "LOG": str(log), **env})
    return r, (log.read_text().splitlines() if log.exists() else [])


@pytest.mark.parametrize("args, runs", TABLE, ids=[" ".join(a) for a, _ in TABLE])
def test_each_command_runs_its_script_with_its_flags(folder, args, runs):
    r, calls = homeair(folder, *args)
    assert r.returncode == 0, r.stderr
    assert calls == runs


def test_every_command_in_the_table_is_in_help_and_help_in_the_table(folder):
    r, calls = homeair(folder, "help")
    listed = {line.split()[1] for line in r.stdout.splitlines() if line.strip().startswith("./homeair ")}
    assert r.returncode == 0 and calls == []
    assert listed == {args[0] for args, _ in TABLE} | {"help"}


@pytest.mark.parametrize("args", [[], ["--help"]])
def test_no_command_shows_help(folder, args):
    r, calls = homeair(folder, *args)
    assert r.returncode == 0 and calls == [] and "./homeair setup" in r.stdout


@pytest.mark.parametrize("args", [["bogus"], ["skill"], ["skill", "on"], ["connect", "now"], ["monitors", "list"]])
def test_an_unknown_command_says_so_and_shows_help(folder, args):
    r, calls = homeair(folder, *args)
    assert r.returncode == 1 and calls == []
    assert f"Unknown command: ./homeair {' '.join(args)}" in r.stderr and "./homeair setup" in r.stderr


def test_check_stops_when_the_settings_have_a_problem(folder):
    r, calls = homeair(folder, "check", FAIL_CHECK="1")
    assert r.returncode == 1 and calls == ["scripts/check_config.py"]


def test_it_runs_from_any_folder_and_the_real_one_is_executable():
    assert os.access(ROOT / "homeair", os.X_OK)
    r = subprocess.run([str(ROOT / "homeair"), "help"], cwd="/", capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and "./homeair setup" in r.stdout
