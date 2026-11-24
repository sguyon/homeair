"""SETUP.md lines from the 14 Nov rehearsals (Alex in Claude Code, Marcus in Codex), approved by Sacha ("ok for a)")."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def setup_md():
    return (ROOT / "SETUP.md").read_text()


def section(title):
    return setup_md().split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def test_setup_asks_the_user_nothing_and_is_one_command():
    # The 14 Nov rehearsals lost turns on the address and profile questions (buttons that sent no text, a box that picked one)
    # and on a second window for the token: setup now asks nothing, and the page asks for what's left.
    text = setup_md()
    assert "Setup asks the user nothing" in text.split("## Get ready", 1)[0]
    assert "```bash\nscripts/run.sh scripts/setup.py\n```" in section("Set it up")
    assert "Describe your home" not in text and "Connect AirGradient" not in text


def test_the_home_server_steps_without_offering_ssh():
    # Codex asked for the SSH address to copy the folder itself
    again = section("Use it again")
    assert "Don't offer to copy it over SSH" in again


def test_the_check_uses_the_address_the_app_printed():
    # Claude Code checked localhost:5555, which another server held, while the app ran elsewhere
    assert "Use the address it prints" in section("Set it up")
