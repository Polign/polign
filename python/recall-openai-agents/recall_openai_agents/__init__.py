"""Recall by Polign for the OpenAI Agents SDK.

``RecallSession`` keeps a run's conversation history in Recall and hands it
back on the next run, the SDK's ``Session`` protocol over Recall's State API.
"""

from .session import RecallSession

__all__ = ["RecallSession"]

try:
    from importlib.metadata import version as _version

    __version__ = _version("recall-openai-agents")
except Exception:  # pragma: no cover - source checkout without metadata
    __version__ = "0.1.0"
