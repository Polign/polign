"""Durable tools over a fake Recall client, inside a real LangGraph agent.

A crash is a ``Killed`` raised from inside a tool: it is a BaseException, so
nothing in the graph or the wrapper handles it, the way nothing runs in a
process that is killed. The next "process" is a new RecallResume on the same
fake client once the lease is free.
"""

import warnings
from typing import Annotated

import pytest
from langchain_core.tools import InjectedToolArg, ToolException, tool
from langgraph.checkpoint.memory import InMemorySaver
from polign_recall import ActionEnd

from recall_langgraph import BRIEFING_ID, RecallResume, recall_tools
from tests.scripted import ScriptedChatModel, seen_text

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from langgraph.prebuilt import create_react_agent


class Killed(BaseException):
    """The process dies here."""


class Shop:
    """The outside world: a mail server that delivers every send, and an
    invoice store that keeps one invoice per idempotency key."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.invoices: dict[str, str] = {}
        self.writes = 0
        self.lookups = 0
        self.kill_after: str | None = None  # a tool name: die right after its effect

    def tools(self):
        shop = self

        @tool
        def lookup_order(order: str) -> str:
            """Look up an order."""
            shop.lookups += 1
            if shop.kill_after == "lookup_order":
                raise Killed()
            return f"order {order}: brake pads, ready"

        @tool
        def save_invoice(invoice: str, total: int, idempotency_key: Annotated[str, InjectedToolArg] = "") -> str:
            """Save an invoice."""
            shop.writes += 1
            shop.invoices.setdefault(idempotency_key or f"nokey-{shop.writes}", f"{invoice}:{total}")
            if shop.kill_after == "save_invoice":
                raise Killed()
            return f"saved {invoice}"

        @tool
        def send_email(to: str, subject: str) -> str:
            """Send an email."""
            shop.sent.append(subject)
            if shop.kill_after == "send_email":
                raise Killed()
            return "sent"

        @tool
        def charge_card(amount: int) -> str:
            """Charge the card on file."""
            raise ToolException("card declined")

        charge_card.handle_tool_error = True
        return [lookup_order, save_invoice, send_email, charge_card]


CLASSES = {"lookup_order": "pure", "save_invoice": "idempotent", "send_email": "external", "charge_card": "external"}


def agent(model, resume, shop, **kwargs):
    tools = resume.durable(shop.tools(), CLASSES)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return create_react_agent(model, [*tools, *recall_tools(resume)], pre_model_hook=resume.pre_model_hook,
                                  post_model_hook=resume.post_model_hook, **kwargs)


def die(client, resume, graph_input, model, shop):
    """Run until a tool kills the process. The lease is left as a dead
    process leaves it, then lapses."""
    resume.open()
    graph = agent(model, resume, shop)
    with pytest.raises(Killed):
        graph.invoke(graph_input)
    client.held_by = None


@pytest.fixture
def shop():
    return Shop()


def test_every_tool_needs_a_class(client, shop):
    resume = RecallResume("shop-1", client=client)
    with pytest.raises(ValueError, match="Unclassified: send_email"):
        resume.durable(shop.tools(), {k: v for k, v in CLASSES.items() if k != "send_email"})
    with pytest.raises(ValueError, match="not given: refund"):
        resume.durable(shop.tools(), {**CLASSES, "refund": "external"})
    with pytest.raises(ValueError, match="has class 'safe'"):
        resume.durable(shop.tools(), {**CLASSES, "send_email": "safe"})
    # A tool can carry its class itself.
    lookup = shop.tools()[0]
    lookup.metadata = {"recall_class": "pure"}
    assert [t.name for t in resume.durable([lookup])] == ["lookup_order"]


def test_each_call_is_an_action_and_its_result_is_recorded_once(client, shop):
    model = ScriptedChatModel([
        ("lookup_order", {"order": "A-17"}),
        ("save_invoice", {"invoice": "inv-17", "total": 410}),
        ("send_email", {"to": "maria@example.com", "subject": "Your car is ready"}),
        ("charge_card", {"amount": 410}),
        "All done.",
    ])
    with RecallResume("shop-1", client=client) as resume:
        result = agent(model, resume, shop).invoke({"messages": [("user", "Close out order A-17.")]})
    assert result["messages"][-1].content == "All done."

    actions = list(client.actions.values())
    assert [(a.tool, a.class_, a.status) for a in actions] == [
        ("lookup_order", "pure", "done"), ("save_invoice", "idempotent", "done"),
        ("send_email", "external", "done"), ("charge_card", "external", "failed"),
    ]
    assert actions[0].result == "order A-17: brake pads, ready"
    assert "card declined" in actions[3].error
    # The idempotent tool got the action's key, and the model never saw it.
    assert list(shop.invoices) == [actions[1].key]
    assert "idempotency_key" not in actions[1].args
    # Each tool result is one turn: the wrapper records it, and the recorder
    # does not record the ToolMessage again when it arrives.
    tool_turns = [(t.name, t.content) for t in client.turns if t.role == "tool"]
    assert [n for n, _ in tool_turns] == ["lookup_order", "save_invoice", "send_email", "charge_card"]
    assert all(t.message_id.startswith("tool-call:") for t in client.turns if t.role == "tool")


def test_an_email_sent_just_before_a_crash_is_not_sent_again(client, shop):
    shop.kill_after = "send_email"
    model = ScriptedChatModel([("send_email", {"to": "maria@example.com", "subject": "Your car is ready"})])
    die(client, RecallResume("shop-1", client=client), {"messages": [("user", "Tell Maria.")]}, model, shop)
    assert shop.sent == ["Your car is ready"]

    # Without a resolver the call is flagged, not repeated: the model reads
    # it first in the briefing.
    model = ScriptedChatModel(["I'll check the sent folder before resending."])
    with RecallResume("shop-1", client=client) as resume:
        agent(model, resume, shop).invoke({"messages": [("user", "Carry on.")]})
        briefing = model.inputs[0][0]
        assert briefing.id == BRIEFING_ID
        assert briefing.content.startswith("Unfinished: send_email(")
        assert [a.status for a in resume.context.pending_actions] == ["unknown"]
    assert shop.sent == ["Your car is ready"]


def test_a_resolver_settles_an_external_call_before_the_model_sees_it(client, shop):
    shop.kill_after = "send_email"
    model = ScriptedChatModel([("send_email", {"to": "maria@example.com", "subject": "Your car is ready"})])
    die(client, RecallResume("shop-1", client=client), {"messages": [("user", "Tell Maria.")]}, model, shop)

    def sent_folder(action):
        subject = action.args  # JSON text of the call's arguments
        if any(s in subject for s in shop.sent):
            return ActionEnd("done", resolution="found in the sent folder")
        return ActionEnd("failed", resolution="not in the sent folder")

    model = ScriptedChatModel(["Maria was told already."])
    with RecallResume("shop-1", client=client, resolver=sent_folder) as resume:
        agent(model, resume, shop).invoke({"messages": [("user", "Carry on.")]})
        assert "Unfinished" not in seen_text(model.inputs[0])
        assert resume.settle().closed[0].resolution == "found in the sent folder"
    assert shop.sent == ["Your car is ready"]
    assert list(client.actions.values())[0].status == "done"


def test_an_interrupted_idempotent_call_is_run_again_with_its_key(client, shop):
    shop.kill_after = "save_invoice"
    model = ScriptedChatModel([("save_invoice", {"invoice": "inv-17", "total": 410})])
    die(client, RecallResume("shop-1", client=client), {"messages": [("user", "Invoice it.")]}, model, shop)
    shop.kill_after = None
    first_key = next(iter(shop.invoices))

    model = ScriptedChatModel(["Invoice saved."])
    with RecallResume("shop-1", client=client) as resume:
        agent(model, resume, shop).invoke({"messages": [("user", "Carry on.")]})
        # Re-run before the first briefing, so the model sees the result as
        # history and nothing left to settle.
        seen = seen_text(model.inputs[0])
        assert "Unfinished" not in seen and "saved inv-17" in seen
    assert shop.writes == 2 and list(shop.invoices) == [first_key]  # two calls, one invoice
    action = list(client.actions.values())[0]
    assert action.status == "done" and action.resolution == "re-run with the same key after a crash"


def test_an_interrupted_read_is_not_run_again(client, shop):
    shop.kill_after = "lookup_order"
    model = ScriptedChatModel([("lookup_order", {"order": "A-17"})])
    die(client, RecallResume("shop-1", client=client), {"messages": [("user", "Look it up.")]}, model, shop)
    with RecallResume("shop-1", client=client) as resume:
        resume.open()
        assert resume.settle().closed == ()  # the resume already abandoned it
        assert "Unfinished" not in resume.briefing
    assert shop.lookups == 1
    assert list(client.actions.values())[0].status == "abandoned"


def test_a_crash_after_the_result_is_recorded_but_before_the_action_closes(client, shop, monkeypatch):
    model = ScriptedChatModel([("send_email", {"to": "maria@example.com", "subject": "Your car is ready"})])
    resume = RecallResume("shop-1", client=client)
    resume.open()

    def killed(*args, **kwargs):
        raise Killed()

    monkeypatch.setattr(resume.agent, "end_action", killed)
    graph = agent(model, resume, shop)
    with pytest.raises(Killed):
        graph.invoke({"messages": [("user", "Tell Maria.")]})
    monkeypatch.undo()
    client.held_by = None

    # The result made it into the turns, and the action is still open, so
    # the next instance sees both: what came back, and that it was never
    # confirmed.
    model = ScriptedChatModel(["The email went out."])
    with RecallResume("shop-1", client=client) as resume:
        agent(model, resume, shop).invoke({"messages": [("user", "Carry on.")]})
        seen = seen_text(model.inputs[0])
        assert "Unfinished: send_email(" in seen
    assert [t.content for t in client.turns if t.role == "tool"] == ["sent"]
    assert shop.sent == ["Your car is ready"]


async def test_durable_tools_work_async(client, shop):
    model = ScriptedChatModel([("save_invoice", {"invoice": "inv-9", "total": 90}), "Done."])
    async with RecallResume("shop-1", client=client) as resume:
        graph = agent(model, resume, shop, checkpointer=InMemorySaver())
        await graph.ainvoke({"messages": [("user", "Invoice it.")]}, {"configurable": {"thread_id": "t"}})
    action = list(client.actions.values())[0]
    assert action.status == "done" and list(shop.invoices) == [action.key]
