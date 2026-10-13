"""Recall by Polign for Pydantic AI.

``RecallMemory`` is a capability: add it to an ``Agent`` and every run starts
with the subject's remembered facts in the instructions, and the model gets a
``remember`` tool to store new ones and correct old ones. Facts are typed
against Recall's predicate registry, so a new value replaces the old one and
the history of the change is kept.
"""

from .capability import RecallMemory, coerce, load_registry
from .render import DEFAULT_TEMPLATE, PredicateSpec, render_beliefs

__all__ = [
    "DEFAULT_TEMPLATE",
    "PredicateSpec",
    "RecallMemory",
    "coerce",
    "load_registry",
    "render_beliefs",
]

try:
    from importlib.metadata import version as _version

    __version__ = _version("recall-pydantic-ai")
except Exception:  # pragma: no cover - source checkout without metadata
    __version__ = "0.1.0"
