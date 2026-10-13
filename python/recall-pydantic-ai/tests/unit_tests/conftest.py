import os

import pytest

from .fake_client import FakeClient

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()
