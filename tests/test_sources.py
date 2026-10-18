"""Prebuilt SITL download and how Hangar finds binaries and parameter files."""

import json

from hangar import paths, sources
from hangar.catalog import Catalog
from hangar.settings import Settings


def fake_server(tmp_path):
    root = tmp_path / "server"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "arduplane").write_bytes(b"\x7fELF fake")
    (root / "plane.parm").write_text("ARSPD_USE 1\n")
    return root


def make_source(root) -> sources.Source:
    return sources.Source(
        id="ardupilot_sitl", title="t", url="file://", ref="4.7.2", license="GPL-3.0", purpose="",
        kind="download", dir="ardupilot-sitl",
        files={"bin/arduplane": (root / "bin" / "arduplane").as_uri(),
               "autotest/models/plane.parm": (root / "plane.parm").as_uri()},
    )


def test_download_installs_atomically_and_locks(tmp_path):
    src = make_source(fake_server(tmp_path))
    assert sources._download(src, progress=lambda *_: None) == "4.7.2"
    exe = paths.sources_dir() / "ardupilot-sitl" / "bin" / "arduplane"
    assert exe.read_bytes() == b"\x7fELF fake" and exe.stat().st_mode & 0o111
    assert src.fetched
    lock = json.loads((paths.sources_dir() / "lock.json").read_text())
    assert lock["ardupilot_sitl"]["ref"] == "4.7.2" and "bin/arduplane" in lock["ardupilot_sitl"]["sha256"]
    assert not list(paths.sources_dir().glob(".download-*"))  # no staging leftovers


def test_failed_download_keeps_previous_install(tmp_path):
    root = fake_server(tmp_path)
    src = make_source(root)
    sources._download(src, progress=lambda *_: None)
    src.files["bin/arducopter"] = (root / "missing").as_uri()
    try:
        sources._download(src, progress=lambda *_: None)
    except OSError:
        pass
    assert (paths.sources_dir() / "ardupilot-sitl" / "bin" / "arduplane").exists()
    assert not list(paths.sources_dir().glob(".download-*"))


def test_prebuilt_is_used_when_no_tree_is_configured(tmp_path):
    sources._download(make_source(fake_server(tmp_path)), progress=lambda *_: None)
    s = Settings()
    assert s.resolved_bin_dir() == paths.sources_dir() / "ardupilot-sitl" / "bin"
    cat = Catalog(s)
    res = cat.resolve_params(cat.get("sitl:plane"))
    assert [p.name for p in res.files] == ["plane.parm"] and not res.missing


def test_own_build_wins_over_prebuilt(tmp_path):
    sources._download(make_source(fake_server(tmp_path)), progress=lambda *_: None)
    tree = tmp_path / "ardupilot"
    (tree / "build" / "sitl" / "bin").mkdir(parents=True)
    (tree / "Tools" / "autotest").mkdir(parents=True)
    s = Settings(ardupilot_dir=str(tree))
    assert s.resolved_bin_dir() == tree / "build" / "sitl" / "bin"
    assert s.autotest_dir() == tree / "Tools" / "autotest"


def test_manifest_is_consistent():
    manifest = sources.load_manifest()
    assert [k for k, s in manifest.items() if s.required] == ["ardupilot_sitl"]
    sitl = manifest["ardupilot_sitl"]
    assert {"bin/arducopter", "bin/arduplane", "bin/ardurover", "autotest/models/plane.parm"} <= set(sitl.files)
    assert all(sitl.ref in url for url in sitl.files.values())  # everything pinned to one version


def make_archive(path, members: dict[str, bytes]):
    import io
    import tarfile

    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def archive_source(url: str) -> sources.Source:
    return sources.Source(id="pack", title="t", url="", ref="0.1.0", license="MIT", purpose="", kind="archive",
                          dir="pack", files={"archive": url})


def test_archive_is_unpacked_without_its_top_folder(tmp_path):
    tgz = tmp_path / "pack.tar.gz"
    make_archive(tgz, {"pack-0.1.0/worlds/a/a.sdf": b"<sdf/>", "pack-0.1.0/README.md": b"hi"})
    sources._archive(archive_source(tgz.as_uri()), progress=lambda *_: None)
    dest = paths.sources_dir() / "pack"
    assert (dest / "worlds" / "a" / "a.sdf").read_bytes() == b"<sdf/>"
    assert (dest / ".hangar-fetched").exists()
    lock = json.loads((paths.sources_dir() / "lock.json").read_text())
    assert len(lock["pack"]["sha256"]["archive"]) == 64


def test_archive_with_unsafe_paths_is_refused(tmp_path):
    import tarfile

    tgz = tmp_path / "evil.tar.gz"
    make_archive(tgz, {"../outside.txt": b"no"})
    try:
        sources._archive(archive_source(tgz.as_uri()), progress=lambda *_: None)
    except tarfile.TarError:
        pass
    assert not (paths.sources_dir().parent / "outside.txt").exists()
    assert not (paths.sources_dir() / "pack").exists()


def test_pack_worlds_are_found(settings):
    from hangar.catalog import Catalog

    world = paths.sources_dir() / "hangar-worlds" / "worlds" / "airfield"
    world.mkdir(parents=True)
    (world / "airfield.sdf").write_text('<sdf version="1.9"><world name="airfield"/></sdf>')
    assert "airfield" in Catalog(settings).worlds
