"""Opt-in evidence for the two-call live test. Never records full webhook payloads."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from recall_vapi import RecallVapi, StateError


class AuditedRecallVapi(RecallVapi):
    def __init__(self, *, audit_path: Path, **kwargs):
        super().__init__(**kwargs)
        self.audit_path = audit_path

    async def handle(self, payload):
        response = await super().handle(payload)
        message = payload["message"]
        kind = message.get("type")
        call = message.get("call", {})
        if kind not in ("assistant-request", "tool-calls", "end-of-call-report"):
            return response
        # Local synthetic checks use verify-* IDs; never count those as live calls.
        if call.get("id", "").startswith("verify-"):
            return response
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "type": kind,
            "call_id": call.get("id"),
            "call_type": call.get("type"),
        }
        if kind == "assistant-request":
            try:
                entry["caller_key"] = self.state.subject(call["id"])
            except StateError:
                entry["caller_key"] = None
            entry["loaded_names"] = []
            for item in response["assistant"]["model"].get("messages", []):
                content = item.get("content", "")
                if not isinstance(content, str) or "<recall_memory>\n" not in content:
                    continue
                block = content.split("<recall_memory>\n", 1)[1].split(
                    "\n</recall_memory>", 1
                )[0]
                try:
                    facts = json.loads(block)
                except ValueError:
                    continue
                entry["loaded_names"] = [
                    f["value"] for f in facts if f.get("predicate") == "name"
                ]
        elif kind == "tool-calls":
            entry["name_writes"] = []
            entry["tool_errors"] = 0
            for result in response["results"]:
                if "error" in result:
                    entry["tool_errors"] += 1
                    continue
                try:
                    value = json.loads(result["result"])
                except (ValueError, TypeError):
                    continue
                if (
                    isinstance(value, dict)
                    and value.get("predicate") == "name"
                    and value.get("saved")
                ):
                    entry["name_writes"].append(
                        {
                            "tool_id": result["toolCallId"],
                            "value": value["value"],
                            "superseded": value.get("superseded", []),
                        }
                    )
        else:
            entry["ended_reason"] = message.get("endedReason", call.get("endedReason"))
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.audit_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as audit:
            audit.write(json.dumps(entry, ensure_ascii=True) + "\n")
        return response


def evaluate(events, first_call, second_call, original="Alex", corrected="Sam"):
    """Evaluate explicit call IDs; never call a pending or synthetic run a pass."""
    if first_call == second_call or any(
        c.startswith("verify-") for c in (first_call, second_call)
    ):
        raise ValueError("choose two distinct live call IDs")
    first = [e for e in events if e["call_id"] == first_call]
    second = [e for e in events if e["call_id"] == second_call]
    starts = [
        [e for e in group if e["type"] == "assistant-request"]
        for group in (first, second)
    ]
    writes = []
    seen = set()
    for event in first:
        for write in event.get("name_writes", []):
            if write["tool_id"] not in seen:
                writes.append(write)
                seen.add(write["tool_id"])
    correction = any(
        w["value"] == corrected
        and original in w["superseded"]
        and any(previous["value"] == original for previous in writes[:i])
        for i, w in enumerate(writes)
    )
    checks = {
        "both_inbound_calls_observed": all(
            group and all(e.get("call_type") == "inboundPhoneCall" for e in group)
            for group in starts
        ),
        "same_resolved_caller": bool(
            all(starts)
            and starts[0][0].get("caller_key")
            and all(
                e.get("caller_key") == starts[0][0]["caller_key"]
                for group in starts
                for e in group
            )
        ),
        "original_then_correction_saved": correction,
        "second_call_loaded_corrected_name": bool(
            starts[1] and all(e.get("loaded_names") == [corrected] for e in starts[1])
        ),
        "both_calls_ended": all(
            any(e["type"] == "end-of-call-report" for e in group)
            for group in (first, second)
        ),
        "no_tool_errors": not any(e.get("tool_errors", 0) for e in first + second),
        "second_call_after_first_ended": bool(
            starts[1]
            and any(
                e["type"] == "end-of-call-report" and e["time"] < starts[1][0]["time"]
                for e in first
            )
        ),
    }
    return {
        "result": "passed" if all(checks.values()) else "incomplete-or-failed",
        "checks": checks,
        "scope": "Webhook and memory evidence only; confirm the spoken greeting by listening.",
    }
