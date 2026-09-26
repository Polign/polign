import pytest

from tests.unit_tests.fake_client import FakeClient


@pytest.fixture
def client():
    """A fake agent-mode Recall client, fresh for every test."""
    return FakeClient()
