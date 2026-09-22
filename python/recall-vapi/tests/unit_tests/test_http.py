import httpx
import pytest
from conftest import event, write
from fastapi import FastAPI

from recall_vapi.fastapi import create_router


async def test_http_authentication_and_tool_response(adapter, client):
    app = FastAPI()
    app.include_router(create_router(adapter, token="test-secret"))
    adapter.bind_call("call-1", "tenant:alice")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        payload = event("tool-calls", toolCallList=[write()])
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            assert (
                await http.post("/vapi/webhook", json=payload, headers=headers)
            ).status_code == 401
        assert not client.writes
        headers = {"Authorization": "Bearer test-secret"}
        response = await http.post("/vapi/webhook", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["results"][0]["toolCallId"] == "tool-1"
        assert "result" in response.json()["results"][0]
        duplicate = await http.post("/vapi/webhook", json=payload, headers=headers)
        assert duplicate.json() == response.json()
        assert len(client.writes) == 1
        assert (await http.post("/vapi/webhook", content="{", headers=headers)).status_code == 400
        assert (await http.post("/vapi/webhook", json=[], headers=headers)).status_code == 400
        assert (
            await http.post("/vapi/webhook", content=b"x" * 1_048_577, headers=headers)
        ).status_code == 413


def test_auth_cannot_be_disabled(adapter):
    with pytest.raises(ValueError):
        create_router(adapter, token="")
