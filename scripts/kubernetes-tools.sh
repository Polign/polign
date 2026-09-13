#!/usr/bin/env bash
# Install pinned tools in the directory supplied by the caller; no system writes.
set -euo pipefail
destination="${1:?tool directory required}"
mkdir -p "$destination"
platform=$(uname -s | tr '[:upper:]' '[:lower:]')
architecture=$(uname -m)
case "$architecture" in
  aarch64|arm64) architecture=arm64 ;;
  x86_64) architecture=amd64 ;;
  *) echo "Unsupported architecture: $architecture" >&2; exit 1 ;;
esac
helm_version=v3.22.0
kind_version=v0.33.0
curl -fsSL "https://get.helm.sh/helm-${helm_version}-${platform}-${architecture}.tar.gz" -o "$destination/helm.tar.gz"
curl -fsSL "https://get.helm.sh/helm-${helm_version}-${platform}-${architecture}.tar.gz.sha256sum" -o "$destination/helm.sha256"
curl -fsSL "https://kind.sigs.k8s.io/dl/${kind_version}/kind-${platform}-${architecture}" -o "$destination/kind"
curl -fsSL "https://kind.sigs.k8s.io/dl/${kind_version}/kind-${platform}-${architecture}.sha256sum" -o "$destination/kind.sha256"
python3 - "$destination" "$platform" "$architecture" <<'PY'
import hashlib, pathlib, sys, tarfile
root=pathlib.Path(sys.argv[1])
for name in ('helm', 'kind'):
    artifact=root/('helm.tar.gz' if name=='helm' else 'kind')
    expected=(root/(name+'.sha256')).read_text().split()[0]
    if hashlib.sha256(artifact.read_bytes()).hexdigest()!=expected:
        raise SystemExit(name+' checksum mismatch')
with tarfile.open(root/'helm.tar.gz') as archive:
    (root/'helm').write_bytes(archive.extractfile(sys.argv[2]+'-'+sys.argv[3]+'/helm').read())
for name in ('helm', 'kind'):
    (root/name).chmod(0o755)
PY
