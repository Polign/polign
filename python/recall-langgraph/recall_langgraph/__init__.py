"""Recall by Polign for LangGraph.

``RecallResume`` resumes an agent from its own records: its working state,
pointers to its work, relevant memories and recent turns come back as a
briefing for the model, and the run's messages are recorded as turns for the
next resume. ``recall_tools`` gives the model the tools that keep those
records current. It sits beside your checkpointer; it does not replace it.
"""

from .resume import BRIEFING_ID, RecallResume
from .tools import recall_tools

__all__ = ["BRIEFING_ID", "RecallResume", "recall_tools"]

try:
    from importlib.metadata import version as _version

    __version__ = _version("recall-langgraph")
except Exception:  # pragma: no cover - source checkout without metadata
    __version__ = "0.1.0"
