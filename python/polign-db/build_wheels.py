#!/usr/bin/env python3
"""Repackage a polign_db release as one PyPI wheel per platform.

Nothing is compiled here. The script downloads the release archives that the
server release already published and signed, checks each against the
release's checksums.txt, and writes wheels that install `polign` and
`polign-server` into the environment's scripts directory, the same way the
ruff and uv wheels ship their binaries.

    python build_wheels.py --version 0.7.0 --out dist
    python build_wheels.py --version 0.7.0 --archives ./downloads   # no network

A packaging-only fix is published as a post release: `--version 0.7.0.post1`
repackages the 0.7.0 binaries. Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import re
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASES = "https://github.com/Polign/polign/releases/download"
BINARIES = ("polign", "polign-server")

# Release archive -> wheel platform tags. The binaries are static (CGO off),
# so one Linux build serves glibc and musl. Go 1.25 needs macOS 12.
PLATFORMS = {
    "polign_db_darwin_arm64.tar.gz": ["macosx_12_0_arm64"],
    "polign_db_darwin_amd64.tar.gz": ["macosx_12_0_x86_64"],
    "polign_db_linux_amd64.tar.gz": ["manylinux2014_x86_64", "manylinux_2_17_x86_64", "musllinux_1_1_x86_64"],
    "polign_db_linux_arm64.tar.gz": ["manylinux2014_aarch64", "manylinux_2_17_aarch64", "musllinux_1_1_aarch64"],
    "polign_db_windows_amd64.zip": ["win_amd64"],
    "polign_db_windows_arm64.zip": ["win_arm64"],
}

METADATA = """\
Metadata-Version: 2.4
Name: polign-db
Version: {version}
Summary: The polign_db server and CLI binaries, installable with pip
Author: Polign
License-Expression: LicenseRef-Polign-Software-License
License-File: LICENSE
Keywords: polign,vector database,agent memory,recall
Project-URL: Homepage, https://polign.com
Project-URL: Documentation, https://polign.com/developers.html
Project-URL: Repository, https://github.com/Polign/polign
Project-URL: Issues, https://github.com/Polign/polign/issues
Classifier: Development Status :: 4 - Beta
Classifier: Intended Audience :: Developers
Classifier: Programming Language :: Go
Classifier: Topic :: Database :: Database Engines/Servers
Requires-Python: >=3.9
Description-Content-Type: text/markdown

{readme}"""


def server_version(version: str) -> str:
    """The server release a package version repackages: 0.7.0.post1 -> 0.7.0."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(\.post\d+)?", version)
    if not match:
        raise SystemExit(f"version must look like 0.7.0 or 0.7.0.post1, got {version!r}")
    return match.group(1)


def fetch(name: str, release: str, archives: Path | None) -> bytes:
    if archives is not None:
        return (archives / name).read_bytes()
    url = f"{RELEASES}/v{release}/{name}"
    print(f"  downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url) as response:  # noqa: S310 - fixed https host
        return response.read()


def checksums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def members(archive_name: str, data: bytes) -> dict[str, bytes]:
    """Basename -> bytes for the files the wheel needs."""
    wanted = {"LICENSE"} | {b + ext for b in BINARIES for ext in ("", ".exe")}
    found: dict[str, bytes] = {}
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                base = info.filename.rsplit("/", 1)[-1]
                if base in wanted and not info.is_dir():
                    found[base] = zf.read(info)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf:
                base = member.name.rsplit("/", 1)[-1]
                if base in wanted and member.isfile():
                    found[base] = tf.extractfile(member).read()  # type: ignore[union-attr]
    return found


def build_wheel(archive_name: str, data: bytes, version: str, out: Path) -> Path:
    tags = PLATFORMS[archive_name]
    ext = ".exe" if archive_name.endswith(".zip") else ""
    files = members(archive_name, data)
    missing = [n for n in ("LICENSE", *(b + ext for b in BINARIES)) if n not in files]
    if missing:
        raise SystemExit(f"{archive_name} is missing {', '.join(missing)}")

    dist_info = f"polign_db-{version}.dist-info"
    scripts = f"polign_db-{version}.data/scripts"
    init = (HERE / "polign_db" / "__init__.py").read_text()
    init, replaced = re.subn(r'^__version__ = "[^"]*".*$', f'__version__ = "{version}"', init, flags=re.M)
    if replaced != 1:
        raise SystemExit("polign_db/__init__.py has no __version__ line to stamp")

    # (path, bytes, executable)
    entries: list[tuple[str, bytes, bool]] = [
        ("polign_db/__init__.py", init.encode(), False),
        ("polign_db/py.typed", b"", False),
    ]
    entries += [(f"{scripts}/{b}{ext}", files[b + ext], True) for b in BINARIES]
    entries += [
        (f"{dist_info}/METADATA", METADATA.format(version=version, readme=(HERE / "README.md").read_text()).encode(), False),
        (f"{dist_info}/WHEEL", ("Wheel-Version: 1.0\nGenerator: polign-db build_wheels\nRoot-Is-Purelib: false\n"
                                + "".join(f"Tag: py3-none-{t}\n" for t in tags)).encode(), False),
        (f"{dist_info}/licenses/LICENSE", files["LICENSE"], False),
    ]
    record = "".join(
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(blob).digest()).rstrip(b'=').decode()},{len(blob)}\n"
        for path, blob, _ in entries
    ) + f"{dist_info}/RECORD,,\n"
    entries.append((f"{dist_info}/RECORD", record.encode(), False))

    out.mkdir(parents=True, exist_ok=True)
    wheel = out / f"polign_db-{version}-py3-none-{'.'.join(tags)}.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, blob, executable in entries:
            # A fixed timestamp keeps a rebuild of the same release byte-identical.
            info = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            zf.writestr(info, blob)
    return wheel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True, help="package version, e.g. 0.7.0 or 0.7.0.post1")
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    parser.add_argument("--archives", type=Path, help="directory holding the release archives and checksums.txt, instead of downloading")
    parser.add_argument("--only", action="append", choices=sorted(PLATFORMS), help="build just this archive's wheel (repeatable)")
    args = parser.parse_args(argv)

    release = server_version(args.version)
    sums = checksums(fetch("checksums.txt", release, args.archives).decode())
    for name in args.only or PLATFORMS:
        data = fetch(name, release, args.archives)
        digest = hashlib.sha256(data).hexdigest()
        if sums.get(name) != digest:
            raise SystemExit(f"{name}: sha256 {digest} does not match checksums.txt ({sums.get(name)})")
        wheel = build_wheel(name, data, args.version, args.out)
        print(f"{wheel.name}  {wheel.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
