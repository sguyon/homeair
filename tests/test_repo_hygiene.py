"""The public repo never points to or contains the private notes folder."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The required public FHWA citation has a literal /research/ segment.
# Only this complete source URL is exempt, never a host or URL prefix.
PUBLIC_SOURCE_URLS = {
    "https://www.fhwa.dot.gov/ENVIRONMENT/air_quality/conformity/research/mpe_benefits/mpe02.cfm",
}
URL = re.compile(r"https?://[^\s)>\"'`]+", re.IGNORECASE)
PRIVATE_URL = re.compile(r"(?:/|^)(?:research|internal)(?:[/?#]|$)|github\.com/sguyon/homeair-internal\b", re.IGNORECASE)
MAINTAINER_REFERENCES = {
    "AGENTS.md": {
        # Agents must load the owner's private rules, when installed locally.
        "If `internal/AGENTS.local.md` exists, read it: it holds the maintainer's private rules.",
        # Those rules identify every live service that needs the settings update.
        "If the maintainer's private rules (`internal/AGENTS.local.md`) list services, update all of them.",
    },
}


def private_note_references(text, folders=("research", "internal")):
    """Check URL paths before removing URLs from the local-folder check."""
    hits = []
    local = re.compile(r"(?<![\w.-])(?:" + "|".join(folders) + r")(?:[/?#]|(?=['\"]))", re.IGNORECASE)
    for line in text.splitlines():
        urls = URL.findall(line)
        if (any(u not in PUBLIC_SOURCE_URLS and PRIVATE_URL.search(u) for u in urls)
                or local.search(URL.sub("", line))):
            hits.append(line)
    return hits


def test_private_note_url_and_folder_boundaries():
    for reference in (
        "research/roads.md", "internal/roads.md",
        "https://example.org/research/roads.md", "https://example.org/internal/roads.md",
        "https://github.com/sguyon/homeair-internal",  # no folder suffix needed
        "https://github.com/sguyon/homeair-internal/blob/main/roads.md",
        "https://github.com/sguyon/homeair/blob/main/research/roads.md",
        "https://github.com/sguyon/homeair/blob/main/internal/roads.md",
        *(f"https://github.com/sguyon/homeair/tree/main/{folder}{suffix}"
          for folder in ("internal", "research")
          for suffix in ("", "/", "?plain=1", "#notes", "/?plain=1", "/#notes")),
        *(f"<{tag} {attribute}={quote}{prefix}/{folder}{suffix}{quote}>"
          for tag, attribute in (("a", "href"), ("img", "src"))
          for quote in ("'", '"')
          for prefix in ("https://example.org", "..")
          for folder in ("internal", "research")
          for suffix in ("", "/", "/x", "?plain=1", "#notes")),
        next(iter(PUBLIC_SOURCE_URLS)) + "/internal/roads.md",  # not a prefix exemption
    ):
        assert private_note_references(reference), reference
    for reference in (*PUBLIC_SOURCE_URLS, "https://example.org/paper", "scripts/roads/build.py"):
        assert not private_note_references(reference), reference


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def test_public_files_do_not_point_to_private_notes():
    # this file and the CI workflow name the old folder on purpose, to forbid it
    result = git("grep", "-n", "-i", "-E", "research|internal", "--", ".",
                 ":!tests/test_repo_hygiene.py", ":!.github/workflows/ci.yml")
    assert result.returncode in (0, 1), result.stderr
    # Existing tooling can name the local private folder; private URLs cannot appear anywhere.
    assert not private_note_references(result.stdout, folders=("research",)), result.stdout
    # Public guides and road builds need no private notes, apart from the exact maintainer lines above.
    result = git("grep", "-n", "-i", "-E", "research|internal", "--",
                 "README.md", "SETUP.md", "DEPLOYMENT.md", "AGENTS.md", "docs/*.md", "scripts/roads")
    assert result.returncode in (0, 1), result.stderr
    hits = []
    for reference in result.stdout.splitlines():
        path, _, line = reference.split(":", 2)
        if line not in MAINTAINER_REFERENCES.get(path, set()):
            hits.extend(private_note_references(reference))
    assert not hits, "\n".join(hits)


def test_internal_is_ignored():
    assert git("check-ignore", "-q", "internal/ROADMAP.md").returncode == 0


def test_nothing_private_is_tracked():
    tracked = git("ls-files").stdout.split()
    assert not [f for f in tracked if f.startswith(("internal/", "research/", "build-log/")) or f in ("config.toml", ".env")]


def test_demo_config_is_tracked_and_the_home_config_is_not():
    """Only the root config.toml (someone's home) is private; the demo's config ships with the code."""
    assert "demo/config.toml" in git("ls-files", "demo").stdout.split()
    assert git("check-ignore", "-q", "demo/config.toml").returncode == 1
    assert git("check-ignore", "-q", "config.toml").returncode == 0
    assert git("check-ignore", "-q", "config.toml.bak-20261110-1430").returncode == 0   # setup.py keeps a copy before replacing config
