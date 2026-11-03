"""Loads the plugin the way Hermes does, with the Hermes modules it imports stubbed.

The stubs are the same shapes Hermes provides (agent.memory_provider, agent.secret_scope,
tools.registry, hermes_constants), so the tests run in this repository without a Hermes checkout.
"""

from __future__ import annotations

import contextvars
import importlib.util
import json
import sys
import threading
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


class _MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None: ...

    @abstractmethod
    def get_tool_schemas(self) -> list: ...


@dataclass(frozen=True)
class _RecallStatus:
    provider_label: str
    count: int
    glyph: str = "🧠"


def _spawn_context_thread(target, *, name, daemon=True, args=(), kwargs=None):
    ctx = contextvars.copy_context()
    return threading.Thread(target=lambda *a, **k: ctx.run(target, *a, **k), args=args, kwargs=kwargs, name=name, daemon=daemon)


def _is_trivial_prompt(text):
    stripped = (text or "").strip()
    return not stripped or stripped.startswith("/") or stripped.lower().rstrip("!?.") in {"ok", "thanks", "hi", "yes", "no"}


@pytest.fixture
def secrets():
    """The profile's secrets, as `get_secret` would answer them."""
    return {}


@pytest.fixture
def hermes_home(tmp_path):
    home = tmp_path / "hermes_home"
    home.mkdir()
    return home


@pytest.fixture
def plugin(monkeypatch, secrets, hermes_home):
    modules = {
        "agent": {},
        "agent.memory_provider": {"MemoryProvider": _MemoryProvider, "spawn_context_thread": _spawn_context_thread,
                                  "RecallStatus": _RecallStatus, "is_trivial_prompt": _is_trivial_prompt},
        "agent.secret_scope": {"get_secret": lambda name, default=None: secrets.get(name, default)},
        "tools": {},
        "tools.registry": {"tool_error": lambda message: json.dumps({"error": message})},
        "hermes_constants": {"get_hermes_home": lambda: hermes_home},
    }
    for name, values in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("hermes_plugin_recall", PLUGIN_DIR / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module
