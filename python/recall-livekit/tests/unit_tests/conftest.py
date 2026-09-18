import pathlib
import sys

import pytest

from recall_livekit import RecallMemory

FAKE = pathlib.Path(__file__).with_name("fake_mcp.py")


@pytest.fixture
def memory():
    """A RecallMemory over the fake subprocess, fresh for every test."""
    with RecallMemory.open(command=[sys.executable, "-u", str(FAKE)], timeout=10) as mem:
        yield mem
