import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def fake_repo(tmp_path, version="0.2.0", changelog="## 0.2.0 (2026-11-01)\n\n- New thing\n\n## 0.1.0\n\n- Old\n"):
    (tmp_path / "src" / "hangar").mkdir(parents=True)
    (tmp_path / "src" / "hangar" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n{changelog}")
    return tmp_path


def test_consistent_release(tmp_path):
    root = fake_repo(tmp_path)
    assert release.check("v0.2.0", root) == []
    notes = release.notes("v0.2.0", root)
    assert "- New thing" in notes and "- Old" not in notes
    assert "hangar@v0.2.0" in notes


def test_version_mismatch_and_missing_changelog(tmp_path):
    root = fake_repo(tmp_path, version="0.1.0", changelog="## 0.1.0\n\n- Old\n")
    problems = release.check("v0.2.0", root)
    assert any("__init__.py says 0.1.0" in p for p in problems)
    assert any("no '## 0.2.0' section" in p for p in problems)


def test_bad_tag_and_prerelease(tmp_path):
    assert any("not like" in p for p in release.check("0.2.0", fake_repo(tmp_path)))
    root = fake_repo(tmp_path / "rc", version="0.3.0rc1", changelog="## 0.3.0rc1\n\n- Try\n")
    assert release.check("v0.3.0rc1", root) == []


def test_this_repo_is_consistent():
    """The released tag must keep passing: version and changelog agree."""
    v = release.code_version()
    assert release.changelog_section(v) is not None
