"""build_wheels.py against synthetic release archives: no network, no real binaries."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_wheels", ROOT / "build_wheels.py")
build_wheels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_wheels)

FILES = {"polign": b"cli-binary", "polign-server": b"server-binary", "polign-import": b"unused", "LICENSE": b"license text"}


DOCS = ("LICENSE", "THIRD_PARTY_NOTICES")


def release(directory: Path, skip: str | None = None, extra: dict[str, bytes] | None = None) -> Path:
    """A fake release directory: one tar.gz, one zip, and checksums.txt."""
    files = FILES | (extra or {})
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, blob in files.items():
            if name != skip:
                info = tarfile.TarInfo(name)
                info.size = len(blob)
                tf.addfile(info, io.BytesIO(blob))
    (directory / "polign_db_linux_amd64.tar.gz").write_bytes(buf.getvalue())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in files.items():
            zf.writestr(name if name in DOCS else name + ".exe", blob)
    (directory / "polign_db_windows_amd64.zip").write_bytes(buf.getvalue())
    (directory / "checksums.txt").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(directory.glob("polign_db_*"))))
    return directory


def build(tmp_path: Path, archive: str, version: str = "0.7.0") -> zipfile.ZipFile:
    out = tmp_path / "dist"
    assert build_wheels.main(["--version", version, "--archives", str(tmp_path), "--out", str(out), "--only", archive]) == 0
    (wheel,) = out.glob("*.whl")
    return zipfile.ZipFile(wheel)


def test_linux_wheel(tmp_path: Path) -> None:
    release(tmp_path)
    wheel = build(tmp_path, "polign_db_linux_amd64.tar.gz", "0.7.0.post1")
    assert Path(wheel.filename).name == (
        "polign_db-0.7.0.post1-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.musllinux_1_1_x86_64.whl")
    names = set(wheel.namelist())
    assert "polign_db-0.7.0.post1.data/scripts/polign" in names
    assert "polign_db-0.7.0.post1.data/scripts/polign-server" in names
    assert not any("polign-import" in n for n in names)
    assert wheel.read("polign_db-0.7.0.post1.dist-info/licenses/LICENSE") == b"license text"
    assert b'__version__ = "0.7.0.post1"' in wheel.read("polign_db/__init__.py")
    # pip keeps the mode bits it finds in the archive for scripts.
    assert wheel.getinfo("polign_db-0.7.0.post1.data/scripts/polign").external_attr >> 16 == 0o100755
    tags = [line for line in wheel.read("polign_db-0.7.0.post1.dist-info/WHEEL").decode().splitlines() if line.startswith("Tag: ")]
    assert tags == ["Tag: py3-none-manylinux2014_x86_64", "Tag: py3-none-manylinux_2_17_x86_64", "Tag: py3-none-musllinux_1_1_x86_64"]


def test_wheel_carries_third_party_notices(tmp_path: Path) -> None:
    release(tmp_path, extra={"THIRD_PARTY_NOTICES": b"notices text"})
    for archive in ("polign_db_linux_amd64.tar.gz", "polign_db_windows_amd64.zip"):
        wheel = build(tmp_path, archive)
        assert wheel.read("polign_db-0.7.0.dist-info/licenses/THIRD_PARTY_NOTICES") == b"notices text"
        metadata = wheel.read("polign_db-0.7.0.dist-info/METADATA").decode()
        assert "License-File: LICENSE\nLicense-File: THIRD_PARTY_NOTICES\n" in metadata
        wheel.close()
        for whl in (tmp_path / "dist").glob("*.whl"):
            whl.unlink()


def test_wheel_without_notices_still_builds(tmp_path: Path) -> None:
    release(tmp_path)
    wheel = build(tmp_path, "polign_db_linux_amd64.tar.gz")
    assert not any(n.endswith("THIRD_PARTY_NOTICES") for n in wheel.namelist())
    assert "License-File: LICENSE\nKeywords:" in wheel.read("polign_db-0.7.0.dist-info/METADATA").decode()


def test_record_matches_contents(tmp_path: Path) -> None:
    release(tmp_path)
    wheel = build(tmp_path, "polign_db_linux_amd64.tar.gz")
    rows = [line.split(",") for line in wheel.read("polign_db-0.7.0.dist-info/RECORD").decode().splitlines()]
    assert {row[0] for row in rows} == set(wheel.namelist())
    for path, digest, size in rows:
        if path.endswith("RECORD"):
            continue
        blob = wheel.read(path)
        assert size == str(len(blob))
        assert digest == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(blob).digest()).rstrip(b"=").decode()


def test_windows_wheel_keeps_exe_suffix(tmp_path: Path) -> None:
    release(tmp_path)
    wheel = build(tmp_path, "polign_db_windows_amd64.zip")
    assert Path(wheel.filename).name == "polign_db-0.7.0-py3-none-win_amd64.whl"
    assert wheel.read("polign_db-0.7.0.data/scripts/polign-server.exe") == b"server-binary"


def test_checksum_mismatch_stops_the_build(tmp_path: Path) -> None:
    release(tmp_path)
    (tmp_path / "polign_db_linux_amd64.tar.gz").write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="does not match checksums.txt"):
        build(tmp_path, "polign_db_linux_amd64.tar.gz")


def test_missing_binary_stops_the_build(tmp_path: Path) -> None:
    release(tmp_path, skip="polign-server")
    with pytest.raises(SystemExit, match="missing polign-server"):
        build(tmp_path, "polign_db_linux_amd64.tar.gz")


def test_version_must_be_a_release_or_post_release() -> None:
    assert build_wheels.server_version("0.7.0.post2") == "0.7.0"
    with pytest.raises(SystemExit):
        build_wheels.server_version("0.7.0rc1")
