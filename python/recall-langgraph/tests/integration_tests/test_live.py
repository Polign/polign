"""RecallResume against a real polign-server and the real ``polign mcp -agent`` subprocess."""

import uuid
import warnings

import pytest
from polign_recall import Client, RecallError

from recall_langgraph import BRIEFING_ID, RecallResume, recall_tools
from tests.scripted import ScriptedChatModel

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from langgraph.prebuilt import create_react_agent


def connect(polign, collection):
    url, cli = polign
    return Client(
        command=[str(cli), "mcp", "-memory-only", "-write", "-agent"],
        env={"POLIGN_URL": url, "POLIGN_COLLECTION": collection},
        agent=True,
        timeout=30,
    )


def react_agent(model, resume):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return create_react_agent(model, recall_tools(resume), pre_model_hook=resume.pre_model_hook,
                                  post_model_hook=resume.post_model_hook)


def test_a_second_process_resumes_where_the_first_stopped(polign):
    collection = "recall_lg_" + uuid.uuid4().hex[:8]
    agent_id = "migrator-" + uuid.uuid4().hex[:6]
    model = ScriptedChatModel([
        ("update_working_state", {"goal": "move billing to the v2 API", "plan": ["find call sites", "migrate"]}),
        ("set_pointer", {"name": "wip", "type": "git_ref", "repo": "github.com/acme/billing", "branch": "agent/wip"}),
        ("milestone", {"name": "call sites found", "progress": "12 call sites listed"}),
        "Twelve call sites; starting on charge().",
    ])
    with connect(polign, collection) as first_client:
        with RecallResume(agent_id, client=first_client, lease_ttl=30) as resume:
            assert resume.context.fresh
            result = react_agent(model, resume).invoke({"messages": [("user", "Start the migration.")]})
            assert result["messages"][-1].content == "Twelve call sites; starting on charge()."

            # While the first process holds the agent, a second cannot take it.
            with connect(polign, collection) as other:
                with pytest.raises(RecallError) as caught:
                    RecallResume(agent_id, client=other).open()
                assert caught.value.code == "lease_held"

    # A new process, as after a crash or a reschedule, starts from the records.
    with connect(polign, collection) as second_client:
        later = ScriptedChatModel(["Continuing with charge()."])
        with RecallResume(agent_id, client=second_client) as resume:
            context = resume.context
            assert not context.fresh
            assert context.working_state.goal == "move billing to the v2 API"
            assert context.working_state.last_milestone == "call sites found"
            assert [p.name for p in context.pointers] == ["wip"]
            assert context.turn_seq == 8  # user, 3 calls, 3 results, reply
            react_agent(later, resume).invoke({"messages": [("user", "Carry on.")]})
            briefing = later.inputs[0][0]
            assert briefing.id == BRIEFING_ID
            for text in ("move billing to the v2 API", "agent/wip", "Twelve call sites"):
                assert text in briefing.content
            assert [t.content for t in resume.agent.recent_turns(limit=2)] == ["Carry on.", "Continuing with charge()."]
