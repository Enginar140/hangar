#!/usr/bin/env python3
"""Helpers for the release workflow (.github/workflows/release.yml).

    python scripts/release.py check v0.2.0     # tag matches src/hangar/__init__.py and CHANGELOG.md
    python scripts/release.py notes v0.2.0     # release notes: install lines + the CHANGELOG section
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "Enginar140/hangar"


def code_version(root: Path = ROOT) -> str:
    text = (root / "src" / "hangar" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("no __version__ in src/hangar/__init__.py")
    return m.group(1)


def changelog_section(version: str, root: Path = ROOT) -> str | None:
    """Body of the '## <version> ...' section, without its heading."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\b.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def check(tag: str, root: Path = ROOT) -> list[str]:
    problems = []
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-.]?(?:a|b|rc)\d+)?", tag):
        problems.append(f"tag '{tag}' is not like v1.2.3 or v1.2.3-rc1")
    version = tag.removeprefix("v")
    code = code_version(root)
    if code.replace("-", "") != version.replace("-", ""):
        problems.append(f"tag {tag} but src/hangar/__init__.py says {code}; bump __version__ first")
    if changelog_section(version, root) is None:
        problems.append(f"CHANGELOG.md has no '## {version}' section")
    return problems


def notes(tag: str, root: Path = ROOT) -> str:
    version = tag.removeprefix("v")
    body = changelog_section(version, root) or ""
    return (
        f"## Install\n\n"
        f"```bash\ncurl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | bash\n```\n\n"
        f"This exact version:\n\n"
        f"```bash\nuv tool install git+https://github.com/{REPO}@{tag}\n```\n\n"
        f"## Changes\n\n{body}\n\n"
        f"Full history: [CHANGELOG.md](https://github.com/{REPO}/blob/{tag}/CHANGELOG.md)\n"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("check", "notes"):
        print(__doc__, file=sys.stderr)
        return 2
    cmd, tag = argv
    if cmd == "check":
        problems = check(tag)
        for p in problems:
            print(f"::error::{p}")
        if not problems:
            print(f"{tag}: version and changelog are consistent")
        return 1 if problems else 0
    print(notes(tag))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
