"""SETUP.md lines from the 14 Nov rehearsals (Alex in Claude Code, Marcus in Codex), approved by Sacha ("ok for a)")."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def setup_md():
    return (ROOT / "SETUP.md").read_text()


def section(title):
    return setup_md().split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def test_the_first_look_asks_nothing_and_the_home_questions_follow_the_released_rules():
    # The 14 Nov rehearsals lost turns on question boxes (buttons that sent no text, a box that picked one); the first
    # look asks nothing, and the home's questions (23 Nov, restored) keep the released rules: the address as plain text,
    # the profiles as a typed list where the box picks one.
    text = setup_md()
    assert "Don't ask about the home before `./homeair setup` has run" in text.split("## 1. Get Home Air", 1)[0]
    assert "```bash\n./homeair setup\n```" in section("2. Run the setup command")
    home = section("4. Your home")
    assert "the address and the profiles are always plain text" in home and "Codex's picks one" in home
    assert "Type the numbers, like **1 3**. If none apply, type **none**." in home
    assert "Describe your home" not in text and "Connect AirGradient" not in text


def test_the_home_server_steps_without_offering_ssh():
    # Codex asked for the SSH address to copy the folder itself
    assert "Don't offer to copy the folder over SSH" in section("5. On a home server or a hosted server")


def test_the_check_uses_the_address_the_app_printed():
    # Claude Code checked localhost:5555, which another server held, while the app ran elsewhere
    assert "Use the address it prints" in section("2. Run the setup command")
