"""RecallSession end to end: the ``recall`` binary from the polign-recall
wheel and the ``polign-server`` it starts for a local directory. Skipped
when the binary is not installed."""

import json
import os
import signal
import uuid

import pytest
from agents import Agent, Runner
from agents.testing import ScriptedModel, assistant_message
from polign_recall import Client
from polign_recall.client import recall_bin

from recall_openai_agents import RecallSession

pytestmark = pytest.mark.skipif(recall_bin() is None, reason="the recall binary is not installed")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    directory = tmp_path_factory.mktemp("recall-data")
    try:
        yield directory
    finally:
        # local_dir starts a detached polign-server that outlives the clients.
        try:
            with open(directory / "runtime.json") as f:
                os.kill(json.load(f)["pid"], signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass


async def test_two_runs_then_another_process(data):
    env = {"RECALL_COLLECTION": "recall_oa_" + uuid.uuid4().hex[:8]}
    model = ScriptedModel()
    agent = Agent(name="test", model=model)
    with RecallSession("live-1", local_dir=data, env=env, lease_ttl=5) as session:
        model.enqueue([assistant_message("San Francisco")])
        await Runner.run(agent, "What city is the Golden Gate Bridge in?", session=session)
        model.enqueue([assistant_message("California")])
        result = await Runner.run(agent, "What state is it in?", session=session)
        assert result.final_output == "California"
        assert len(model.calls[-1].input) == 3
        assert len(await session.get_items()) == 4

    with Client(local_dir=data, agent=True, env=env) as client:
        with RecallSession("live-1", client=client) as session:
            items = await session.get_items()
            assert [i.get("role") for i in items] == ["user", "assistant", "user", "assistant"]
            assert (await session.pop_item())["role"] == "assistant"
            assert len(await session.get_items()) == 3
        # The conversation is a record of the Recall agent too.
        with client.resume("oa-live-1") as resumed:
            turns = resumed.recent_turns()
            assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
            assert "Golden Gate" in resumed.context.briefing
