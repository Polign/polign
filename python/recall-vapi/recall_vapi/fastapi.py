"""Optional FastAPI transport. Install recall-vapi[fastapi] to use this module."""

import asyncio
import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from polign import PolignError

from .adapter import RecallVapi
from .state import StateError


def create_router(adapter: RecallVapi, *, token: str, path: str = "/vapi/webhook") -> APIRouter:
    """Authenticate a Vapi server's Authorization: Bearer header before dispatch.

    Use the same token in the saved phone number's server configuration and the
    memory tools' explicit server.headers. Never expose it in browser code.
    """
    if not token or not token.strip():
        raise ValueError("a webhook bearer token is required")
    expected = ("Bearer " + token).encode()
    router = APIRouter()

    @router.post(path)
    async def webhook(request: Request) -> dict:
        actual = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(actual, expected):
            raise HTTPException(401, "Invalid webhook credential")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1_048_576:
                raise HTTPException(413, "Webhook exceeds 1 MiB")
        try:
            payload = json.loads(body)
            return await asyncio.wait_for(adapter.handle(payload), timeout=6)
        except StateError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Invalid Vapi message") from exc
        except PolignError as exc:
            # Call state could not be read or written; a retry finds any pending record.
            raise HTTPException(503, "Call state unavailable; retry") from exc
        except asyncio.TimeoutError as exc:
            # Shielded writes continue and the call state prevents duplicate execution.
            raise HTTPException(504, "Webhook timed out; writes may still complete") from exc

    return router
