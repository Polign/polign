"""A stand-in for ``polign mcp -memory-only`` used by the unit tests.

Speaks just enough MCP over stdio for ``polign_recall.Client``: an in-memory
belief store with single-valued supersession, multi-valued append, subject
recall with optional word-overlap search, forget, and the registry. Subjects
named ``slow`` stall and ``broken`` answer with an error, for failure tests.

It also answers the agent tools of ``polign mcp -agent``: resume, release,
working state and turns, with a lease that runs out after its TTL unless it is
released. That lets one fake stand in for two workers, the second resuming
after the first one "died" without releasing. An agent id starting with
``down`` fails to resume, for outage tests.
"""

import json
import sys
import time

REGISTRY = {
    "name": {"cardinality": "single", "value_type": "string", "description": "The name the caller asks to be called"},
    "timezone": {"cardinality": "single", "value_type": "string", "description": "The caller's timezone or city"},
    "age": {"cardinality": "single", "value_type": "number", "description": "The caller's age in years"},
    "consents_to_recording": {"cardinality": "single", "value_type": "boolean", "description": "Whether the caller agreed to recording"},
    "open_issue": {"cardinality": "multi", "value_type": "string", "description": "A problem the caller reported that is not resolved"},
}

beliefs: list[dict] = []  # current beliefs, in insertion order
counter = 0


def belief(subject, predicate, value):
    global counter
    counter += 1
    return {
        "subject": subject, "predicate": predicate, "value": value, "confidence": 1.0,
        "source": "user_stated", "kind": "fact",
        "observed_at": f"2026-09-18T00:00:{counter:02d}Z", "event_id": f"e{counter}",
    }


def remember(args):
    subject, predicate, value = args["subject"], args["predicate"], args["value"]
    if predicate not in REGISTRY:
        raise ValueError(f"unknown predicate {predicate}")
    current = [b for b in beliefs if b["subject"] == subject and b["predicate"] == predicate]
    for b in current:
        if b["value"] == value:
            return {"stored": b, "already_known": True, "superseded": []}
    superseded = []
    if REGISTRY[predicate]["cardinality"] == "single":
        for b in current:
            beliefs.remove(b)
            superseded.append(b)
    stored = belief(subject, predicate, value)
    beliefs.append(stored)
    return {"stored": stored, "already_known": False, "superseded": superseded}


def recall(args):
    subject = args.get("subject")
    query = args.get("query")
    limit = args.get("limit") or 20
    out = [b for b in beliefs if subject is None or b["subject"] == subject]
    if args.get("predicate"):
        out = [b for b in out if b["predicate"] == args["predicate"]]
    if query:
        words = set(query.lower().split())
        out = [b for b in out if words & set(str(b["value"]).lower().split())
               or words & set(b["predicate"].lower().split("_"))]
    return out[:limit]


def forget(args):
    subject, predicate = args["subject"], args["predicate"]
    matches = [b for b in beliefs if b["subject"] == subject and b["predicate"] == predicate]
    if "value" in args:  # no value means withdraw every value of that predicate
        matches = [b for b in matches if b["value"] == args["value"]]
    for b in matches:
        beliefs.remove(b)
    return {"withdrawn": len(matches)}


agents: dict[str, dict] = {}  # agent id -> turns, working state, lease expiry


def agent_record(agent_id):
    return agents.setdefault(agent_id, {"turns": [], "state": {}, "version": 0, "expires": 0.0, "epoch": 0})


def briefing(record):
    lines = ["# Resuming your work"]
    state = record["state"]
    for key in ("goal", "progress", "focus"):
        if state.get(key):
            lines.append(f"{key.capitalize()}: {state[key]}")
    if record["turns"]:
        lines.append("## Your most recent turns, verbatim")
        lines.extend(f"[{t['role']} #{t['seq']}] {t['content']}" for t in record["turns"][-5:])
    return "\n".join(lines)


def agent_tool(name, args):
    agent_id = args["agent_id"]
    if name == "agent_resume":
        if agent_id.startswith("down"):
            raise ValueError("backend unavailable")
        ttl = args.get("lease_ttl_seconds") or 60
        if not 5 <= ttl <= 3600:
            raise ValueError("polign: HTTP 400: invalid argument: ttl must be between 5s and 1h0m0s")
        record = agent_record(agent_id)
        record["resume_args"] = args
        record["ttl"] = ttl
        if args.get("defer_lease"):
            # Read now, write only after agent_acquire.
            record["pending"] = True
        else:
            if record["expires"] > time.monotonic():
                raise ValueError("recall: agent lease is held by another process")
            record["expires"] = time.monotonic() + ttl
            record["epoch"] += 1
        return {"agent_id": agent_id, "fresh": not record["turns"] and not record["state"],
                "epoch": 0 if record.get("pending") else record["epoch"],
                "lease_held": not record.get("pending"), "working_state": record["state"] or None,
                "recent_turns": record["turns"][-5:], "omitted": {}, "turn_seq": len(record["turns"]),
                "token_budget": args.get("token_budget") or 8000, "tokens": 10, "briefing": briefing(record)}
    record = agent_record(agent_id)
    if name == "agent_acquire":
        if record.get("pending"):
            if record["expires"] > time.monotonic():
                raise ValueError("recall: agent lease is held by another process")
            record["pending"] = False
            record["expires"] = time.monotonic() + record["ttl"]
            record["epoch"] += 1
        return {"acquired": True, "epoch": record["epoch"]}
    if name in ("update_working_state", "record_turn") and record.get("pending"):
        raise ValueError("recall: agent lease not acquired yet; call AcquireLease before writing")
    if name == "agent_release":
        released = record["expires"] > time.monotonic()
        record["expires"] = 0.0
        return {"released": released}
    if name == "update_working_state":
        record["state"].update({k: v for k, v in args.items() if k != "agent_id"})
        record["version"] += 1
        return {**record["state"], "version": record["version"]}
    if name == "record_turn":
        turn = {"seq": len(record["turns"]) + 1, "role": args["role"], "content": args["content"],
                "at": "2026-09-26T00:00:00Z"}
        if args.get("name"):
            turn["name"] = args["name"]
        record["turns"].append(turn)
        return turn
    if name == "recent_turns":
        return record["turns"][-(args.get("limit") or 20):]
    raise ValueError(f"no agent tool {name}")


def handle(name, args):
    if "agent_id" in args:
        return {"content": [{"type": "text", "text": json.dumps(agent_tool(name, args))}]}
    subject = args.get("subject")
    if subject == "slow":
        time.sleep(0.5)
    if subject == "broken":
        return {"isError": True, "content": [{"type": "text", "text": json.dumps({"error": "backend unavailable"})}]}
    if name == "list_predicates":
        payload = [{"predicate": k, **v} for k, v in REGISTRY.items()]
    elif name == "remember":
        payload = remember(args)
    elif name == "recall":
        payload = recall(args)
    elif name == "forget":
        payload = forget(args)
    elif name == "memory_history":
        payload = []
    else:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps({"error": f"no tool {name}"})}]}
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def main():
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        if request["method"] == "tools/call":
            params = request["params"]
            try:
                result = handle(params["name"], params.get("arguments") or {})
            except ValueError as exc:
                result = {"isError": True, "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
