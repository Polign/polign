"""Recall by Polign as a Hermes Agent memory provider.

Memory lives in Recall: a new statement replaces the old one, the old one
stays in the history, and memory can be read as of any earlier time. The
agent gets five tools (recall_search, recall_profile, recall_remember,
recall_forget, recall_history), relevant memories in front of each turn, and
each completed turn sent to Recall for extraction in the background.

Where memory lives, in order:
  RECALL_URL (+ RECALL_API_KEY)        a `recall serve` you run; shared across agents
  RECALL_BACKEND / RECALL_BACKEND_URL  a database you chose (local dir, polign, pgvector, ...)
  neither                              a local database under $HERMES_HOME/recall

The extraction model (RECALL_EXTRACT_MODEL, or extract_model in
$HERMES_HOME/recall.json) works out the facts in each turn so that a later
statement can replace an earlier one. Blank picks a model from the keys in
.env; "none" keeps text as written, which Recall still finds by its words.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from agent.memory_provider import MemoryProvider, spawn_context_thread
from agent.secret_scope import get_secret

try:  # Hermes 2026-10 and later; older builds have no recall indicator
    from agent.memory_provider import RecallStatus, is_trivial_prompt
except ImportError:  # pragma: no cover - older Hermes
    RecallStatus = None  # type: ignore[assignment]

    def is_trivial_prompt(text: str | None) -> bool:
        return not (text or "").strip()

try:
    from tools.registry import tool_error
except ImportError:  # pragma: no cover - older Hermes

    def tool_error(message: str) -> str:
        return json.dumps({"error": message})

logger = logging.getLogger(__name__)

PROVIDER_NAME = "recall"
PROVIDER_LABEL = "Recall"
CONFIG_FILE = "recall.json"
LOCAL_DIR = "recall"

# How many memories go in front of a turn, and how long prefetch() waits for
# the background recall started at turn start before the turn goes on without it.
MAX_PREFETCH_MEMORIES = 8
PREFETCH_WAIT_SECS = 3.0
# A turn longer than this is cut at its last sentence end so the extraction
# model sees the whole thing; see Recall's Ollama context note.
SYNC_MSG_MAX_CHARS = 4000
# Writes queue behind one background worker; past this many the oldest are
# kept and the newest dropped with a warning, so a dead server cannot grow the
# queue without bound.
MAX_QUEUED_WRITES = 32
# After this many consecutive failures, Recall is left alone for the cooldown.
BREAKER_THRESHOLD, BREAKER_COOLDOWN_SECS = 5, 120.0

_SENTENCE_ENDS = (".", "!", "?", "\n", "。", "！", "？")
_SHUTDOWN = object()


def _truncate(text: str, max_len: int = SYNC_MSG_MAX_CHARS) -> str:
    """Cut `text` at its last sentence end inside `max_len`; a hard cut when there is none."""
    if len(text) <= max_len:
        return text
    window = text[:max_len]
    cut = max(window.rfind(sep) for sep in _SENTENCE_ENDS)
    return text[: cut + 1].rstrip() if cut > max_len // 3 else window


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _load_file_config(hermes_home: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(hermes_home) / CONFIG_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pick_extract_model(secret: Callable[[str], str]) -> str:
    """A model for the keys in .env: Anthropic first, then OpenAI, else none."""
    if secret("ANTHROPIC_API_KEY"):
        return "anthropic:claude-haiku-5-5"
    if secret("OPENAI_API_KEY"):
        return "openai:gpt-5.4-mini"
    return ""


def resolve_settings(hermes_home: str, secret: Callable[[str], str]) -> dict[str, str]:
    """What the environment and $HERMES_HOME/recall.json say, as one mapping.

    Keys: url, api_key, token_file, backend, backend_url, backend_key,
    extract_model, extract_key_name, extract_key. Every value is a string;
    empty means unset. `extract_model` "none" means keep text as written.
    """
    file_cfg = _load_file_config(hermes_home)
    url = secret("RECALL_URL") or _clean(file_cfg.get("url"))
    model = secret("RECALL_EXTRACT_MODEL") or _clean(file_cfg.get("extract_model"))
    if not model and not url:
        model = _pick_extract_model(secret)
    if model.lower() == "none":
        model = ""
    key_name = ""
    if model.startswith("anthropic:"):
        key_name = "ANTHROPIC_API_KEY"
    elif model.startswith("openai:"):
        key_name = "OPENAI_API_KEY"
    backend_url = secret("RECALL_BACKEND_URL") or _clean(file_cfg.get("backend_url"))
    backend = secret("RECALL_BACKEND") or _clean(file_cfg.get("backend"))
    if not backend and not backend_url:
        backend, backend_url = "local", str(Path(hermes_home) / LOCAL_DIR)
    return {
        "url": url.rstrip("/"),
        "api_key": secret("RECALL_API_KEY"),
        "token_file": secret("RECALL_TOKEN_FILE"),
        "backend": backend,
        "backend_url": backend_url,
        "backend_key": secret("RECALL_BACKEND_KEY"),
        "extract_model": model,
        "extract_key_name": key_name,
        "extract_key": secret(key_name) if key_name else "",
    }


def open_client(settings: dict[str, str], timeout: float = 120.0):
    """A polign_recall Client for `settings`: HTTP to a recall serve, or a local process."""
    from polign_recall import Client

    if settings["url"]:
        return Client(url=settings["url"], api_key=settings["api_key"] or None,
                      token_file=settings["token_file"] or None, timeout=timeout)
    # The recall process gets the extraction key and nothing else from the
    # Hermes process: inherit_env=False keeps every other provider key and bot
    # token in .env out of it (polign-recall 0.25 or later).
    env = {settings["extract_key_name"]: settings["extract_key"]} if settings["extract_key_name"] and settings["extract_key"] else None
    return Client(text=True, backend=settings["backend"] or None, backend_url=settings["backend_url"] or None,
                  backend_key=settings["backend_key"] or None, extract_model=settings["extract_model"] or None,
                  env=env, inherit_env=False, timeout=timeout)


# -- Rendering ---------------------------------------------------------------

def describe_memory(m: Any) -> str:
    """One memory as a line: the statement, when it was stated, and what it replaced."""
    parts = [f"stated {m.since}"]
    for b in m.before or ():
        parts.append(f'replaced "{b.text}" from {b.since}')
    for e in getattr(m, "elsewhere", None) or ():
        parts.append(f'also believed: "{e.text}" from {e.since}')
    return f"{m.text} ({'; '.join(parts)})"


def render_context(memories: list[Any], limit: int = MAX_PREFETCH_MEMORIES) -> str:
    picked = list(memories)[: max(1, limit)]
    if not picked:
        return ""
    return "\n".join([
        "## Recall Memory",
        "Remembered from earlier conversations, and possibly relevant now. A newer statement "
        "replaces an older one; where a value was replaced, the old one is shown so you can "
        "mention the correction when it matters. Use recall_search to ask for more.",
        *(f"- {describe_memory(m)}" for m in picked),
    ])


def memory_dict(m: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"text": m.text, "since": m.since, "days_ago": m.days_ago}
    if m.before:
        out["before"] = [{"text": b.text, "since": b.since} for b in m.before]
    if getattr(m, "elsewhere", None):
        out["elsewhere"] = [{"text": e.text, "since": e.since} for e in m.elsewhere]
    if getattr(m, "said", ""):
        out["said"] = m.said
    return out


def remember_dict(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"remembered": [
        {"text": r.text, **({"replaces": list(r.replaces)} if r.replaces else {}),
         **({"already_known": True} if r.already_known else {})}
        for r in result.remembered]}
    if result.kept_as_text:
        out["kept_as_text"] = True
        if not result.remembered:
            out["note"] = "Kept as written. Recall finds it by its words; set an extraction model so new statements can replace old ones."
    return out


def timeline_dict(t: Any) -> dict[str, Any]:
    return {"about": t.about, "now": list(t.now),
            "changes": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(s).items() if v not in ("", (), None)}
                        for s in t.changes]}


# -- Tools -------------------------------------------------------------------

_INSTANT = "an RFC 3339 instant such as 2026-09-01T00:00:00Z"


def _schema(name: str, description: str, properties: dict[str, dict[str, str]] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": list(required)}}


TOOL_SCHEMAS = [
    _schema("recall_search",
            "Search long-term memory by meaning. Each memory says when it was stated and what it replaced, "
            "so a correction shows both the current value and the old one. Call it before answering anything "
            "that may depend on the user's preferences, facts, people, projects or earlier decisions.",
            {"question": {"type": "string", "description": "What you want to know, in words."},
             "as_of": {"type": "string", "description": f"Answer as of this time rather than now: {_INSTANT}."}},
            ("question",)),
    _schema("recall_profile",
            "Everything currently believed about the user, newest first, with what each statement replaced. "
            "No search; use it for an overview, recall_search for a question."),
    _schema("recall_remember",
            "Store a lasting fact, preference, correction or decision the user stated. A new statement about "
            "the same thing replaces the old one and the result says what it replaced. Call it the moment the "
            "user says something worth keeping; skip chit-chat and things already stored.",
            {"text": {"type": "string", "description": "What to remember, as the user said it."},
             "observed_at": {"type": "string", "description": f"When it was said, as {_INSTANT}. Omit for now."}},
            ("text",)),
    _schema("recall_forget",
            "Withdraw what a description names, such as \"my editor\" or \"where I live\". Returns the "
            "statements withdrawn. Nothing is deleted: history keeps them, as of an earlier time still answers.",
            {"text": {"type": "string", "description": "What to forget, described in words."}}, ("text",)),
    _schema("recall_history",
            "How what is believed about a question changed, statement by statement, oldest first, including "
            "things since forgotten. Use it when the user asks what they said before or when something changed.",
            {"question": {"type": "string", "description": "What to trace, in words."}}, ("question",)),
]

SYSTEM_PROMPT = (
    "# Recall Memory\n"
    "Active. Long-term memory kept in Recall by Polign: a new statement replaces the old one, the old one "
    "stays in the history, and memory can be read as of any earlier time.\n"
    "Call recall_search before answering anything that could depend on prior context (the user's "
    "preferences, facts, people, projects, earlier decisions); do not rely on the chat window alone. "
    "When a memory shows a replaced value, the newer one is current; mention the correction only when it "
    "matters. Call recall_remember when the user states something lasting; recall_forget when asked to "
    "forget; recall_history when asked what changed."
)


class RecallMemoryProvider(MemoryProvider):
    """Recall by Polign: corrections replace, history stays, reads as of any time."""

    def __init__(self, *, client_factory: Callable[[dict[str, str]], Any] | None = None):
        self._client_factory = client_factory or open_client
        self._client: Any = None
        self._settings: dict[str, str] = {}
        self._hermes_home = ""
        self._session_id = ""
        self._primary = True
        self._lock = threading.Lock()  # client, breaker, prefetch state
        self._connect_lock = threading.Lock()  # one open at a time, outside _lock: opening can take seconds
        self._failures, self._breaker_open_until = 0, 0.0
        self._prefetch_query, self._prefetch_result, self._prefetch_count = "", "", 0
        self._prefetch_served = False
        self._prefetch_thread: threading.Thread | None = None
        self._last_status: Any = None
        self._writes: queue.Queue[Any] = queue.Queue()
        self._writer: threading.Thread | None = None
        self._accepting = True  # writes are taken until shutdown starts
        self._closed = False

    # -- identity and availability --------------------------------------------

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @staticmethod
    def _secret(name: str) -> str:
        try:
            return get_secret(name, "") or ""
        except Exception as exc:  # a scope-less multiplex caller: the spawn site is the bug, not this read
            logger.debug("Recall: secret %s unreadable: %s", name, exc)
            return ""

    def is_available(self) -> bool:
        try:
            import polign_recall  # noqa: F401
        except ImportError:
            return False
        return not self.unavailable_reason()

    def unavailable_reason(self) -> str:
        try:
            import polign_recall  # noqa: F401
        except ImportError:
            return "the polign-recall package is not installed (pip install polign-recall)"
        if self._secret("RECALL_URL") and not (self._secret("RECALL_API_KEY") or self._secret("RECALL_TOKEN_FILE")):
            return "RECALL_URL is set but RECALL_API_KEY is not; a recall serve needs a key"
        return ""

    # -- setup -----------------------------------------------------------------

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {"key": "url", "description": "Recall server URL (a `recall serve` you run). Blank keeps memory in a local database under this profile",
             "default": "", "url": "https://polign.com/docs/recall"},
            {"key": "api_key", "description": "Recall key for that server (rcl_...). Not needed for a local database",
             "secret": True, "required": False, "env_var": "RECALL_API_KEY"},
            {"key": "extract_model", "description": "Model that works out the facts in each turn, as provider:model (anthropic:claude-haiku-5-5, openai:gpt-5.4-mini, ollama:llama3.2). Blank picks one from the keys in .env; none keeps text as written",
             "default": ""},
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        path = Path(hermes_home) / CONFIG_FILE
        current = _load_file_config(hermes_home)
        for key in ("url", "extract_model", "backend", "backend_url"):
            if key in values:
                value = _clean(values.get(key))
                if value:
                    current[key] = value
                else:
                    current.pop(key, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def get_status_config(self, provider_config: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = self._settings or resolve_settings(self._hermes_home or str(_hermes_home()), self._secret)
        where = settings["url"] or f"{settings['backend']} {settings['backend_url']}".strip()
        return {"memory": where, "extract_model": settings["extract_model"] or "none (text kept as written)"}

    # -- lifecycle --------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._hermes_home = str(kwargs.get("hermes_home") or _hermes_home())
        self._primary = (kwargs.get("agent_context") or "primary") == "primary"
        self._settings = resolve_settings(self._hermes_home, self._secret)
        if self._writer is None:
            self._writer = spawn_context_thread(self._write_loop, name="recall-writer")
            self._writer.start()
        # Opening can start a local database, or download polign-server on
        # first use; the writer does it so agent startup does not wait.
        self._enqueue("connect", lambda c: None)

    def _connect(self) -> Any:
        """The client, opened on first use and reopened after a transport failure. None while the breaker is open."""
        with self._lock:
            if self._closed or time.monotonic() < self._breaker_open_until:
                return None
            if self._client is not None:
                return self._client
        with self._connect_lock:
            with self._lock:
                if self._client is not None or self._closed:
                    return self._client
            try:
                client = self._client_factory(self._settings)
            except Exception as exc:
                with self._lock:
                    self._note_failure(exc, "connect")
                return None
            with self._lock:
                self._client = client  # failures keep counting until a call succeeds
                return client

    def _note_failure(self, exc: Exception, what: str) -> None:
        """Called with the lock held. Counts toward the breaker; a dead transport drops the client."""
        self._failures += 1
        if getattr(exc, "code", "") == "transport_error" and self._client is not None:
            client, self._client = self._client, None
            _quiet(client.close)
        if self._failures >= BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + BREAKER_COOLDOWN_SECS
            self._failures = 0
            logger.warning("Recall %s failed %d times; leaving it alone for %.0fs: %s", what, BREAKER_THRESHOLD, BREAKER_COOLDOWN_SECS, exc)
        else:
            logger.warning("Recall %s failed: %s", what, exc)

    def _call(self, what: str, fn: Callable[[Any], Any]) -> Any:
        """Run `fn(client)`; failures count toward the breaker and raise."""
        client = self._connect()
        if client is None:
            raise RuntimeError("Recall is unavailable right now" if self._breaker_open_until else "Recall is not connected")
        try:
            result = fn(client)
        except Exception as exc:
            with self._lock:
                self._note_failure(exc, what)
            raise
        with self._lock:
            self._failures = 0
        return result

    def on_session_switch(self, new_session_id: str, **kwargs: Any) -> None:
        self._session_id = new_session_id

    def system_prompt_block(self) -> str:
        return SYSTEM_PROMPT

    # -- recall before the turn --------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        self._start_prefetch(message)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass  # the recall for a turn starts at on_turn_start, with the turn's own message

    def _start_prefetch(self, query: str) -> None:
        if is_trivial_prompt(query):
            return
        with self._lock:
            if self._prefetch_query == query and (self._prefetch_served or self._prefetch_thread is not None):
                return  # already running, or already in front of this turn
            self._prefetch_query, self._prefetch_result, self._prefetch_count = query, "", 0
            self._prefetch_served = False
            self._prefetch_thread = spawn_context_thread(self._run_prefetch, args=(query,), name="recall-prefetch")
            self._prefetch_thread.start()

    def _run_prefetch(self, query: str) -> None:
        try:
            memories = self._call("recall", lambda c: c.ask(query))
        except Exception:
            return
        body = render_context(memories)
        with self._lock:
            if self._prefetch_query == query:
                self._prefetch_result, self._prefetch_count = body, min(len(memories), MAX_PREFETCH_MEMORIES)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """The memories for this turn, once: later calls in the same turn (after tool calls) add nothing."""
        self._last_status = None
        if is_trivial_prompt(query):
            return ""
        self._start_prefetch(query)
        with self._lock:
            if self._prefetch_query == query and self._prefetch_served:
                return ""
            thread = self._prefetch_thread if self._prefetch_query == query else None
        if thread is not None:
            thread.join(timeout=PREFETCH_WAIT_SECS)
        with self._lock:
            if self._prefetch_query != query or thread is None or thread.is_alive():
                return ""  # still running: the turn goes on without it, recall_search remains
            body, count = self._prefetch_result, self._prefetch_count
            self._prefetch_served, self._prefetch_thread = True, None
        if body and RecallStatus is not None:
            self._last_status = RecallStatus(provider_label=PROVIDER_LABEL, count=count)
        return body

    def recall_status(self) -> Any:
        return self._last_status

    # -- capture after the turn ---------------------------------------------------

    def _enqueue(self, what: str, fn: Callable[[Any], Any]) -> bool:
        if not self._accepting:
            return False
        if self._writes.qsize() >= MAX_QUEUED_WRITES:
            logger.warning("Recall %s dropped: %d writes already waiting", what, MAX_QUEUED_WRITES)
            return False
        self._writes.put((what, fn))
        return True

    def _write_loop(self) -> None:
        while True:
            item = self._writes.get()
            try:
                if item is _SHUTDOWN:
                    return
                what, fn = item
                try:
                    self._call(what, fn)
                except Exception:
                    pass  # already logged by _call
            finally:
                self._writes.task_done()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", **kwargs: Any) -> None:
        """Send the turn to Recall for extraction, in the background, one turn at a time."""
        if not self._primary or not (user_content or "").strip():
            return
        messages = [{"role": "user", "content": _truncate(user_content)}]
        if (assistant_content or "").strip():
            messages.append({"role": "assistant", "content": _truncate(assistant_content)})
        self._enqueue("capture", lambda c: c.remember(messages=messages))

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Mirror the built-in MEMORY.md and USER.md writes, so Recall holds them too."""
        if not self._primary:
            return
        content = (content or "").strip()
        if action in ("add", "replace") and content:
            self._enqueue("mirror", lambda c: c.remember(text=_truncate(content)))
        elif action == "remove":
            previous = ((metadata or {}).get("previous_content") or content or "").strip()
            if previous and self._settings.get("extract_model"):
                self._enqueue("mirror", lambda c: c.forget_text(_truncate(previous)))

    def idle(self, timeout: float = 60.0) -> bool:
        """Wait until every queued write has been sent. True when the queue drained in time."""
        deadline = time.monotonic() + timeout
        while self._writes.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    # -- tools ----------------------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [dict(s) for s in TOOL_SCHEMAS]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        handler = _TOOLS.get(tool_name)
        if handler is None:
            return tool_error(f"Unknown tool: {tool_name}")
        try:
            return json.dumps(handler(self, args or {}), ensure_ascii=False)
        except Exception as exc:
            code = getattr(exc, "code", "")
            return tool_error(f"{code}: {exc}" if code else str(exc))

    def _tool_search(self, args: dict[str, Any]) -> dict[str, Any]:
        question = _clean(args.get("question"))
        if not question:
            raise ValueError("question is required")
        as_of = _clean(args.get("as_of")) or None
        memories = self._call("recall", lambda c: c.ask(question, as_of=as_of))
        return {"memories": [memory_dict(m) for m in memories]}

    def _tool_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        memories = self._call("recall", lambda c: c.ask())
        return {"memories": [memory_dict(m) for m in memories]}

    def _tool_remember(self, args: dict[str, Any]) -> dict[str, Any]:
        text = _clean(args.get("text"))
        if not text:
            raise ValueError("text is required")
        observed_at = _clean(args.get("observed_at")) or None
        return remember_dict(self._call("remember", lambda c: c.remember(text=text, observed_at=observed_at)))

    def _tool_forget(self, args: dict[str, Any]) -> dict[str, Any]:
        text = _clean(args.get("text"))
        if not text:
            raise ValueError("text is required")
        return {"forgotten": self._call("forget", lambda c: c.forget_text(text))}

    def _tool_history(self, args: dict[str, Any]) -> dict[str, Any]:
        question = _clean(args.get("question"))
        if not question:
            raise ValueError("question is required")
        return {"history": [timeline_dict(t) for t in self._call("history", lambda c: c.history_text(question))]}

    # -- shutdown ---------------------------------------------------------------------

    def shutdown(self) -> None:
        """Send what is queued, then close. Nothing new is taken once this starts."""
        if not self._accepting:
            return
        self._accepting = False
        writer = self._writer
        if writer is not None:
            self._writes.put(_SHUTDOWN)
            writer.join(timeout=30.0)
        with self._lock:
            self._closed = True
            client, self._client = self._client, None
        if client is not None:
            _quiet(client.close)


_TOOLS: dict[str, Callable[[RecallMemoryProvider, dict[str, Any]], dict[str, Any]]] = {
    "recall_search": RecallMemoryProvider._tool_search,
    "recall_profile": RecallMemoryProvider._tool_profile,
    "recall_remember": RecallMemoryProvider._tool_remember,
    "recall_forget": RecallMemoryProvider._tool_forget,
    "recall_history": RecallMemoryProvider._tool_history,
}


def _quiet(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception as exc:
        logger.debug("Recall: %s", exc)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def register(ctx: Any) -> None:
    """Register Recall as a memory provider. A new instance per load, as the multiplexing contract asks."""
    ctx.register_memory_provider(RecallMemoryProvider())
