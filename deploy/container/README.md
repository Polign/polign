# Polign server container

`ghcr.io/polign/polign-server:0.6.5` supports Linux AMD64 and ARM64. It contains
`polign-server`, `polign-apikey`, and `polign` from the signed binary release,
CA certificates, and the server license. It runs as UID/GID 65532 on a pinned
distroless base. Kubernetes installation is documented in `charts/polign`.

The source server is compiled in the private `polign_db` release pipeline. After
archive publication and verification, that pipeline dispatches the public
`container-release.yml` workflow. `prepare-release.sh` verifies the release's
Sigstore identity and checksums before extracting either architecture. The image
is tested in kind, published with provenance and an SBOM, and signed by Cosign.
No private source is copied into the packaging context or image.

To inspect and verify a release (Cosign 3.1.3+):

```sh
docker buildx imagetools inspect ghcr.io/polign/polign-server:0.6.5
cosign verify \
  --certificate-identity=https://github.com/Polign/polign/.github/workflows/container-release.yml@refs/heads/main \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com \
  ghcr.io/polign/polign-server:0.6.5
```

Use the resulting image digest in `image.digest` for immutable deployment.
Chart versions are independent of server versions: `polign-chart-v0.1.0` packages
chart 0.1.0 with default server 0.6.5. Chart releases never become the default
binary download release.
