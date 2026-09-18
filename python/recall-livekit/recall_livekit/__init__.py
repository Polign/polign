"""Recall by Polign for LiveKit Agents.

One ``RecallMemory`` per worker process owns the Recall subprocess. Each
caller gets a ``SubjectMemory`` view on it, and ``RecallAgent`` (or ``attach``
for an existing Agent subclass) loads that caller's facts before the first
reply and exposes a ``remember`` tool so the model can store new ones.
"""

from importlib import resources

from .agent import RecallAgent
from .hooks import MemoryBinding, attach
from .memory import PredicateSpec, RecallMemory, SubjectMemory
from .render import DEFAULT_TEMPLATE, compose_instructions, render_beliefs
from .tools import build_forget_tool, build_remember_tool

__all__ = [
    "DEFAULT_TEMPLATE",
    "MemoryBinding",
    "PredicateSpec",
    "RecallAgent",
    "RecallMemory",
    "SubjectMemory",
    "VOICE_REGISTRY",
    "attach",
    "build_forget_tool",
    "build_remember_tool",
    "compose_instructions",
    "render_beliefs",
]

try:
    from importlib.metadata import version as _version

    __version__ = _version("recall-livekit")
except Exception:  # pragma: no cover - source checkout without metadata
    __version__ = "0.1.0"

#: Path of the starter predicate registry for phone and voice callers. Pass it
#: as ``predicates=`` to :meth:`RecallMemory.open` or set ``POLIGN_PREDICATES``.
VOICE_REGISTRY = str(resources.files(__name__).joinpath("registry_voice.json"))
