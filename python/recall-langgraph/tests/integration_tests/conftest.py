"""The integration tests run against the real ``recall`` binary from the
polign-recall wheel and the ``polign-server`` it starts for a local
directory; they are skipped when the binary is not installed."""

import json
import os
import signal
import uuid

import pytest
from polign_recall import Client
from polign_recall.client import recall_bin


def pytest_collection_modifyitems(items):
    if recall_bin() is None:
        skip = pytest.mark.skip(reason="the recall binary is not installed")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def data(tmp_path_factory):
    """A directory with its own polign-server, stopped after the session."""
    directory = tmp_path_factory.mktemp("recall-data")
    try:
        yield directory
    finally:
        # local_dir starts a detached polign-server that outlives the clients.
        try:
            with open(directory / "runtime.json") as f:
                os.kill(json.load(f)["pid"], signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass


def collection(prefix):
    return "recall_" + prefix + "_" + uuid.uuid4().hex[:8]


def connect(data, collection, timeout=60):
    """An agent-mode client on ``data``'s server, in its own collection."""
    return Client(local_dir=data, agent=True, env={"RECALL_COLLECTION": collection}, timeout=timeout)
