"""Recall by Polign for Pydantic AI.

``RecallMemory`` is a capability: add it to an ``Agent`` and every run starts
with the subject's remembered facts in the instructions, and the model gets a
``remember`` tool to store new ones and correct old ones. A new statement
replaces the old one and the history of the change is kept. It works over
Recall's typed tools (facts typed against the predicate registry, one subject
per run) and its text tools (passages in the user's words, one user per
collection).
"""

from .capability import RecallMemory, coerce, is_text, load_registry
from .render import DEFAULT_TEMPLATE, PredicateSpec, render_beliefs, render_memories

__all__ = [
    "DEFAULT_TEMPLATE",
    "PredicateSpec",
    "RecallMemory",
    "coerce",
    "is_text",
    "load_registry",
    "render_beliefs",
    "render_memories",
]

try:
    from importlib.metadata import version as _version

    __version__ = _version("recall-pydantic-ai")
except Exception:  # pragma: no cover - source checkout without metadata
    __version__ = "0.1.1"
