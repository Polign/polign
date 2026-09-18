"""A stand-in for ``polign mcp -memory-only`` used by the unit tests.

Speaks just enough MCP over stdio for ``polign_recall.Client``: an in-memory
belief store with single-valued supersession, multi-valued append, subject
recall with optional word-overlap search, forget, and the registry. Subjects
named ``slow`` stall and ``broken`` answer with an error, for failure tests.
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


def handle(name, args):
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
