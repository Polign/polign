"""LangChain tools that let the model write what its next instance resumes from."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from polign_recall import RecallError

from .resume import RecallResume

T = TypeVar("T")

PointerType = Literal["git_ref", "object", "env", "external", "process"]


def _call(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except RecallError as exc:
        # The model sees this text as the tool's result and can react to it.
        raise ToolException(str(exc)) from exc


def _tool(fn: Callable[..., str]) -> BaseTool:
    async def coroutine(**kwargs: Any) -> str:
        return await asyncio.to_thread(fn, **kwargs)

    return StructuredTool.from_function(
        func=fn, coroutine=coroutine, parse_docstring=True, handle_tool_error=True
    )


def recall_tools(resume: RecallResume) -> list[BaseTool]:
    """The tools to give the model: ``update_working_state``, ``milestone``,
    ``fetch_output`` and ``set_pointer``. Each works with ``invoke`` and
    ``ainvoke``. A failed call comes back to the model as the tool's text."""

    def update_working_state(
        goal: str | None = None,
        plan: list[str] | None = None,
        progress: str | None = None,
        focus: str | None = None,
        decisions: list[str] | None = None,
        open_questions: list[str] | None = None,
        notes: str | None = None,
    ) -> str:
        """Save your working state: the note your next instance resumes from if this run stops.

        Fields you give replace the current ones; fields you leave out are kept. Write it when
        your plan or progress changes, not after every step, as a colleague taking over would
        need it.

        Args:
            goal: What the whole task is for.
            plan: The remaining steps, in order.
            progress: What is done so far.
            focus: What you are doing right now.
            decisions: Decisions made, each with its reason.
            open_questions: What is still unknown.
            notes: Anything else the next instance needs.
        """
        given = {
            "goal": goal, "plan": plan, "progress": progress, "focus": focus,
            "decisions": decisions, "open_questions": open_questions, "notes": notes,
        }
        state = _call(lambda: resume.update_working_state(**{k: v for k, v in given.items() if v is not None}))
        return f"Working state saved (version {state.version})."

    def milestone(name: str, progress: str | None = None) -> str:
        """Declare a durable point worth keeping, such as tests passing or a section drafted.

        Not every step: a milestone marks a stretch of work done.

        If this run stops, the next instance loses at most the work since the last milestone.

        Args:
            name: The milestone reached.
            progress: What is now done.
        """
        _call(lambda: resume.milestone(name, progress))
        return f"Milestone recorded: {name}."

    def fetch_output(ref: str) -> str:
        """Return a stored output in full, by the ref shown in your context.

        Args:
            ref: The output's ref.
        """
        return _call(lambda: resume.fetch_output(ref)).content

    def set_pointer(
        name: str,
        type: PointerType,
        repo: str | None = None,
        branch: str | None = None,
        sha: str | None = None,
        uri: str | None = None,
        image: str | None = None,
        lockfile_hash: str | None = None,
        setup: str | None = None,
        kind: str | None = None,
        id: str | None = None,
        url: str | None = None,
        command: str | None = None,
        port: str | None = None,
        note: str | None = None,
    ) -> str:
        """Record where a piece of your work lives, so your next instance can find it.

        Replaces any pointer with the same name. Each type needs one of its fields:
        git_ref (repo, branch, sha; needs sha or branch), object (uri), env (image,
        lockfile_hash, setup), external (kind, id, url; needs id or url), process
        (command, port; needs command).

        Args:
            name: A stable name for this piece of work.
            type: Where it lives.
            repo: git_ref: the repository.
            branch: git_ref: the branch.
            sha: git_ref: the commit.
            uri: object: where the object is stored.
            image: env: the container image.
            lockfile_hash: env: the hash of the dependency lockfile.
            setup: env: how to set the environment up.
            kind: external: what kind of thing it is, such as a ticket.
            id: external: its id.
            url: external: its URL.
            command: process: the command that runs it.
            port: process: the port it listens on.
            note: What it is.
        """
        given = {
            "repo": repo, "branch": branch, "sha": sha, "uri": uri, "image": image,
            "lockfile_hash": lockfile_hash, "setup": setup, "kind": kind, "id": id,
            "url": url, "command": command, "port": port,
        }
        pointer = _call(lambda: resume.set_pointer(name, type, {k: v for k, v in given.items() if v}, note))
        return f"Pointer {pointer.name} saved."

    return [_tool(f) for f in (update_working_state, milestone, fetch_output, set_pointer)]
