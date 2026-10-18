"""Recall by Polign for LangGraph.

``RecallResume`` resumes an agent from its own records: its working state,
pointers to its work, relevant memories and recent turns come back as a
briefing for the model, and the run's messages are recorded as turns for the
next resume. ``recall_tools`` gives the model the tools that keep those
records current. ``RecallResume.durable`` wraps the tools that touch the
world, so a crash in the middle of a call is settled on resume instead of
guessed at. It sits beside your checkpointer; it does not replace it.
"""

from polign_recall import Action, ActionEnd

from .durable import DurableTool
from .resume import BRIEFING_ID, RecallResume
from .tools import recall_tools

__all__ = ["Action", "ActionEnd", "BRIEFING_ID", "DurableTool", "RecallResume", "recall_tools"]

try:
    from importlib.metadata import version as _version

    __version__ = _version("recall-langgraph")
except Exception:  # pragma: no cover - source checkout without metadata
    __version__ = "0.3.0"
