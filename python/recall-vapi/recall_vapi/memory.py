"""Provider-independent Recall operations and schemas for Vapi Function tools."""

from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from importlib import resources
from typing import Any

from polign_recall import Client, RememberResult

VOICE_REGISTRY = str(resources.files(__package__).joinpath("registry_voice.json"))


def _connection(env: dict, local_dir: Any, backend: str | None = None, backend_url: Any = None,
                backend_key: str | None = None, database: Any = None) -> dict[str, str | None] | None:
    """Where the Recall client's polign_db server is, in the terms
    polign.Client takes, or None when memory is in a backend that is not a
    polign_db (call state then needs ``state=``).

    ``database`` is what the recall server reported (``Client.database``,
    recall 0.23 or later) and settles it; without one, the arguments and the
    environment are read the way the older binary read them.
    """
    if database is not None:
        if database.backend == "local":
            with open(os.path.join(database.url, "local-key")) as f:
                return {"url": database.server, "api_key": f.read().strip() or None}
        if database.backend == "polign":
            # The server never reports a key. One given here or in the
            # environment is used; a key the server took from a saved setup
            # is not known here, so pass backend_key or state= for a
            # polign_db that requires one.
            key = backend_key or env.get("RECALL_BACKEND_KEY") or env.get("POLIGN_API_KEY") \
                or os.environ.get("RECALL_BACKEND_KEY") or os.environ.get("POLIGN_API_KEY") or None
            return {"url": database.url, "api_key": key}
        return None
    if local_dir is not None or backend == "local":
        # Recall's managed local server leaves these behind when it starts.
        directory = local_dir if local_dir is not None else backend_url
        if directory is None:
            directory = _recall_home()
        directory = os.fspath(directory)
        with open(os.path.join(directory, "runtime.json")) as f:
            url = json.load(f)["url"]
        with open(os.path.join(directory, "local-key")) as f:
            return {"url": url, "api_key": f.read().strip() or None}
    if backend == "polign":
        return {"url": os.fspath(backend_url) if backend_url is not None else "http://localhost:23000", "api_key": backend_key}
    if backend is not None or backend_url is not None:
        return None
    url = (env.get("RECALL_BACKEND_URL") or env.get("POLIGN_URL") or os.environ.get("RECALL_BACKEND_URL")
           or os.environ.get("POLIGN_URL") or "http://localhost:23000")
    key = env.get("RECALL_BACKEND_KEY") or env.get("POLIGN_API_KEY") or os.environ.get("RECALL_BACKEND_KEY") \
        or os.environ.get("POLIGN_API_KEY") or None
    return {"url": url, "api_key": key}


def _recall_home() -> str:
    """Where a recall binary older than 0.23 keeps the local database when
    nothing names a directory, the same rule polign_recall applies when it
    runs `recall setup -local` for such a binary. A 0.23 binary reports the
    directory itself (Client.database), so this is not consulted for it."""
    return os.environ.get("RECALL_HOME") or os.environ.get("POLIGN_RECALL_HOME") \
        or os.path.join(os.path.expanduser("~"), ".config", "polign", "recall")


class RecallMemory:
    """Wrap a long-lived polign_recall.Client. Call close() during service shutdown."""

    def __init__(self, client: Client, *, connection: dict[str, str | None] | None = None):
        self.client = client
        # {"url": ..., "api_key": ...} for the server behind this client, when known.
        # PolignState.for_memory keeps call state on the same server.
        self.connection = connection
        self.registry = {entry["predicate"]: entry for entry in client.predicates()}
        if not self.registry:
            raise ValueError("Recall must have at least one registered predicate")

    @classmethod
    def open(cls, *, predicates: str = VOICE_REGISTRY, **client_options: Any) -> RecallMemory:
        """Accept Client options: backend, backend_url and backend_key (the
        same three names as recall -backend, -url and -key), local_dir, env,
        command and timeout."""
        env = dict(client_options.pop("env", None) or {})
        env["RECALL_PREDICATES"] = env["POLIGN_PREDICATES"] = predicates
        # The voice registry is typed, so the typed tools are asked for.
        client_options.setdefault("typed", True)
        client = Client(env=env, **client_options)
        try:
            return cls(client, connection=_connection(env, client_options.get("local_dir"), client_options.get("backend"),
                                                      client_options.get("backend_url"), client_options.get("backend_key"),
                                                      getattr(client, "database", None)))
        except BaseException:
            client.close()
            raise

    def close(self) -> None:
        self.client.close()

    def context(self, subject: str, *, limit: int, query: str | None = None) -> str:
        beliefs = self.client.recall(subject=subject, query=query, limit=limit)
        facts = [{"predicate": b.predicate, "value": b.value} for b in beliefs]
        # Escape markup delimiters inside values; remembered text is data, not instructions.
        encoded = json.dumps(facts, ensure_ascii=True, allow_nan=False)
        encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e")
        return "<recall_memory>\n" + encoded + "\n</recall_memory>"

    def coerce(self, predicate: str, value: Any) -> str | float | int | bool:
        spec = self.registry.get(predicate)
        if spec is None:
            raise ValueError("unknown predicate; use one of the registered memory types")
        kind = spec.get("value_type", "string")
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower().strip() in ("true", "false"):
                return value.lower().strip() == "true"
            raise ValueError("this predicate requires true or false")
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError("this predicate requires a finite number")
            try:
                number = float(value)
            except (ValueError, OverflowError):
                raise ValueError("this predicate requires a finite number") from None
            if not math.isfinite(number):
                raise ValueError("this predicate requires a finite number")
            return number
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError(
                "this predicate requires a non-empty string of at most 4096 characters"
            )
        return value.strip()

    def validate(self, name: str, args: dict, *, forget_tool: bool) -> dict:
        if name == "recall":
            if set(args) != {"query"} or not isinstance(args["query"], str):
                raise ValueError("recall requires only a query string")
            if not args["query"].strip() or len(args["query"]) > 4096:
                raise ValueError("query must contain 1 to 4096 characters")
            return args
        if name != "remember" and not (name == "forget" and forget_tool):
            raise ValueError("unknown or disabled memory tool")
        if set(args) != {"predicate", "value"} or not isinstance(args["predicate"], str):
            raise TypeError("memory writes require only predicate and value")
        return {
            "predicate": args["predicate"],
            "value": self.coerce(args["predicate"], args["value"]),
        }

    def execute(self, subject: str, name: str, args: dict, *, limit: int) -> str:
        if name == "recall":
            return self.context(subject, limit=limit, query=args["query"])
        predicate, value = args["predicate"], args["value"]
        if name == "forget":
            withdrawn = self.client.forget(subject, predicate, value)
            return json.dumps({"withdrawn": withdrawn, "predicate": predicate, "value": value})
        result = self.client.remember(subject, predicate, value, source="user_stated")
        if not isinstance(result, RememberResult):
            raise TypeError("unexpected result from typed remember")
        return json.dumps(
            {
                "saved": True,
                "predicate": predicate,
                "value": result.stored.value,
                "superseded": [b.value for b in result.superseded],
                "already_known": result.already_known,
                "event_id": result.stored.event_id,
            },
            allow_nan=False,
        )

    def tools(self, server: dict, *, forget_tool: bool = False) -> list[dict]:
        predicate = {
            "type": "string",
            "enum": list(self.registry),
            "description": "; ".join(
                f"{name}: {spec.get('description', name)} ({spec.get('value_type', 'string')})"
                for name, spec in self.registry.items()
            ),
        }
        writes = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "predicate": predicate,
                "value": {
                    "type": "string",
                    "description": (
                        "The stated fact. Encode numbers as digits and booleans as true or false."
                    ),
                },
            },
            "required": ["predicate", "value"],
        }
        definitions = [
            (
                "remember",
                (
                    "Save a lasting fact the caller explicitly stated. A correction replaces "
                    "a single-valued fact. Wait for success before confirming it was saved."
                ),
                writes,
            ),
            (
                "recall",
                "Search this caller's remembered facts when the initial memory is insufficient.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
        ]
        if forget_tool:
            definitions.append(
                (
                    "forget",
                    (
                        "Withdraw this specific fact at the caller's request. "
                        "Historical events are retained; this is not data erasure."
                    ),
                    writes,
                )
            )
        return [
            {
                "type": "function",
                "async": False,
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": deepcopy(schema),
                },
                "server": deepcopy(server),
            }
            for name, description, schema in definitions
        ]
