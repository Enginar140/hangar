# Releasing

Releases are built and published by `.github/workflows/release.yml` when a
version tag is pushed.

1. Set the new version in `src/hangar/__init__.py` (`__version__ = "0.2.0"`).
   This is the only place it lives; the package reads it from there.
2. Add a `## 0.2.0 (date)` section at the top of `CHANGELOG.md`. It becomes
   the release notes.
3. Commit, push, wait for CI to pass.
4. Tag and push the tag:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

The workflow checks that the tag, `__version__` and the changelog agree (and
stops if they do not), runs lint and tests, builds the wheel and sdist with
SHA256 sums, and creates the GitHub release. Tags like `v0.2.0-rc1` are
published as pre-releases.

To try it without publishing: **Actions → release → Run workflow** with a tag
name. This dry run builds everything and keeps the files as a workflow
artifact. From a shell: `gh workflow run release.yml -f tag=v0.2.0`.

---

**Türkçe özet:** `__init__.py`'deki sürümü artır, CHANGELOG'a o sürümün
bölümünü ekle, commit + push, sonra `git tag v0.2.0 && git push origin v0.2.0`.
Gerisini iş akışı yapar. Sürüm ile CHANGELOG uyuşmazsa hiçbir şey yayınlanmaz.
