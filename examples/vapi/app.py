"""Inbound Vapi voice calls with Recall memory. See README.md for configuration."""

import asyncio
import hashlib
import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from live_test_audit import AuditedRecallVapi
from recall_vapi import RecallMemory, RecallVapi
from recall_vapi.fastapi import create_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.environ["VAPI_WEBHOOK_TOKEN"]
    identity_key = os.environ["CALLER_IDENTITY_KEY"].encode()
    tenant = os.environ["RECALL_TENANT"]
    phone_id = os.environ["VAPI_PHONE_NUMBER_ID"]
    public_url = os.environ["PUBLIC_BASE_URL"].rstrip("/")
    if (
        not token.strip()
        or not identity_key
        or not tenant.strip()
        or not phone_id.strip()
    ):
        raise ValueError(
            "webhook token, identity key, tenant, and phone ID must be set"
        )
    data = Path(os.environ.get("RECALL_DATA_DIR", "./data"))
    data.mkdir(parents=True, exist_ok=True)
    # Supplying POLIGN_URL uses the shared server. Otherwise keep a local database.
    options = {} if os.environ.get("POLIGN_URL") else {"local_dir": data / "memory"}
    memory = await asyncio.to_thread(
        RecallMemory.open,
        timeout=4,
        **options,
        **(
            {"predicates": os.environ["POLIGN_PREDICATES"]}
            if os.environ.get("POLIGN_PREDICATES")
            else {}
        ),
    )
    adapter = None
    try:

        async def resolve_subject(message):
            call = message["call"]
            if (
                call.get("phoneNumberId") != phone_id
                or call.get("type") != "inboundPhoneCall"
            ):
                return None
            number = call.get("customer", {}).get("number", "")
            if not isinstance(number, str) or not re.fullmatch(
                r"\+[1-9]\d{7,14}", number
            ):
                return None
            # Demo identity for low-sensitivity preferences, NOT account authentication.
            identity = json.dumps([tenant, number], separators=(",", ":")).encode()
            return (
                "caller:" + hmac.new(identity_key, identity, hashlib.sha256).hexdigest()
            )

        server = {
            "url": public_url + "/vapi/webhook",
            "headers": {"Authorization": "Bearer " + token},
            "timeoutSeconds": 7,
        }
        adapter_type = (
            AuditedRecallVapi if os.environ.get("VAPI_LIVE_TEST") == "1" else RecallVapi
        )
        audit_options = (
            {"audit_path": data / "live-test/events.jsonl"}
            if adapter_type is AuditedRecallVapi
            else {}
        )
        adapter = adapter_type(
            **audit_options,
            memory=memory,
            assistant={
                "name": "Recall support assistant",
                "firstMessageMode": "assistant-speaks-first-with-model-generated-message",
                "model": {
                    "provider": "openai",
                    "model": os.environ.get("VAPI_MODEL", "gpt-4.1-mini"),
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a friendly support assistant. Greet the caller by their preferred "
                                "name if remembered and ask how you can help. Keep responses short."
                            ),
                        }
                    ],
                },
                "voice": {"provider": "azure", "voiceId": "en-US-JennyNeural"},
                "server": server,
                "serverMessages": ["tool-calls", "end-of-call-report"],
            },
            tool_server=server,
            resolve_subject=resolve_subject,
            forget_tool=True,
        )
        app.include_router(create_router(adapter, token=token))
        app.state.recall = adapter
        yield
    finally:
        if adapter is not None:
            await adapter.aclose()
        else:
            await asyncio.to_thread(memory.close)


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def health():
    return {"status": "ok"}
