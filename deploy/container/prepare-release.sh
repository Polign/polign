#!/usr/bin/env bash
# Requires gh and cosign. Only authenticated release archives enter the image.
set -euo pipefail
version="${1:?server version required, e.g. v0.6.5}"
destination="${2:?container build context required}"
[[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Expected stable vX.Y.Z' >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
gh release download "$version" --repo Polign/polign --dir "$work" \
  --pattern 'polign_db_linux_*.tar.gz' --pattern checksums.txt --pattern checksums.txt.sigstore.json
cosign verify-blob --bundle "$work/checksums.txt.sigstore.json" \
  --certificate-identity="https://github.com/Polign/polign_db/.github/workflows/release.yml@refs/tags/$version" \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com "$work/checksums.txt"
python3 - "$work" "$destination" <<'PY'
import hashlib, pathlib, sys, tarfile
work, destination=map(pathlib.Path, sys.argv[1:])
checksums={parts[1].lstrip('*'):parts[0] for line in (work/'checksums.txt').read_text().splitlines() if (parts:=line.split())}
for arch in ('amd64', 'arm64'):
    archive=work/f'polign_db_linux_{arch}.tar.gz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==checksums[archive.name]
    target=destination/'bin/linux'/arch
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        members={m.name.removeprefix('./'):m for m in tar.getmembers()}
        for name in ('polign-server', 'polign-apikey', 'polign'):
            path=target/name
            path.write_bytes(tar.extractfile(members[name]).read())
            path.chmod(0o755)
        (destination/'bin/LICENSE').write_bytes(tar.extractfile(members['LICENSE']).read())
print('Verified and extracted both Linux architectures')
PY
