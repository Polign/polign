# Developing the Python SDK

Maintainer notes. This file is not part of the published package (the sdist
ships `polign/`, `tests/`, `README.md`, and `LICENSE`; the README is the PyPI
page).

## Tests

```bash
cd python
python -m pip install -e ".[grpc]" pytest
pytest tests/test_unit.py tests/test_grpc_unit.py tests/test_filter_expr.py
pytest tests/test_integration.py -v
```

The integration tests boot a real `polign-server`. By default they download
the latest stable release archive into `~/.cache/polign-python-tests` and
reuse it on later runs. Override with:

| Variable | Effect |
|---|---|
| `POLIGN_SERVER=/path/to/polign-server` | Use this binary |
| `POLIGN_SOURCE=/path/to/polign_db` | Build the server from a source checkout (needs Go) |
| `POLIGN_SERVER_VERSION=v0.6.2` | Download that release instead of the latest |
| `POLIGN_SERVER_VERSION=none` | Never download; fall back to `polign-server` on `PATH` |

CI runs the unit tests on the supported Python range and the integration
tests against the latest release, so a client change that only works against
unreleased server code shows up as a failure. Gate such changes on the server
version until the release ships.

## Regenerating the vendored gRPC stubs

`proto/vectordb.proto` at the repository root is the wire contract. After it
changes, regenerate the stubs from the repository root:

```bash
python -m pip install "grpcio-tools>=1.80" "protobuf>=6.31,<7"
python -m grpc_tools.protoc -I proto \
    --python_out=python/polign/_pb \
    --grpc_python_out=python/polign/_pb \
    --pyi_out=python/polign/_pb \
    proto/vectordb.proto
sed -i '' 's/^import vectordb_pb2 as/from . import vectordb_pb2 as/' \
    python/polign/_pb/vectordb_pb2_grpc.py
```

The `grpc` extra in `pyproject.toml` pins `grpcio` and `protobuf` to the
versions the stubs were generated with. Bump both together.

## Releasing

Bump the version in **both** `pyproject.toml` and `polign/__init__.py`
(`__version__`); they are not linked. Then push a tag shaped
`python/vX.Y.Z`:

```bash
git tag python/v0.7.0
git push origin python/v0.7.0
```

`.github/workflows/python-publish.yml` checks the tag against both version
strings, builds the sdist and wheel, and uploads to PyPI with OIDC trusted
publishing. The PyPI project trusts this repository, that workflow file, and
the `pypi` GitHub environment, so there is no token to rotate.

Bare `vX.Y.Z` tags on this repository are server releases and never publish
the package. The package version line is independent of the server version;
document the minimum server version a feature needs in the README instead.

## Releasing the polign_db binary wheels

`polign_db` (in `polign_db/`) is the one package whose version is the server
version. It has no `pyproject.toml` and nothing to bump: `build_wheels.py`
downloads a server release's archives, checks them against the release's
`checksums.txt`, and repackages `polign` and `polign-server` into one wheel per
platform. After a server release `vX.Y.Z` is out, push a matching tag:

```bash
git tag polign_db/v0.7.0
git push origin polign_db/v0.7.0
```

The `polign_db` job in `python-publish.yml` verifies the release's Sigstore
signature, builds the six wheels, installs the Linux one, starts a local
database with it, and uploads. To ship a packaging fix for the same binaries,
tag a post release such as `polign_db/v0.7.0.post1`.

Each wheel is about 26 MB and PyPI allows a project 10 GB in total, so there is
room for roughly 60 releases before old ones need deleting or the limit needs
raising.

To build one wheel locally and try it:

```bash
python polign_db/build_wheels.py --version 0.7.0 --only polign_db_darwin_arm64.tar.gz --out /tmp/dist
python -m pip install /tmp/dist/polign_db-*.whl
```

`polign-recall` (in the Polign/recall repository) depends on `polign_db`, and
`recall-livekit` depends on `polign-recall`, so a first release goes out in that
order.
