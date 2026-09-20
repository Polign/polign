import asyncio
import sys

import pytest
from polign_recall import RecallError

from recall_livekit import RecallMemory, __version__, render_beliefs


def test_registry_is_read_on_open(memory):
    assert __version__
    assert set(memory.registry) == {"name", "timezone", "age", "consents_to_recording", "open_issue"}
    assert memory.registry["open_issue"].cardinality == "multi"
    assert memory.registry["age"].value_type == "number"


def test_missing_binary_raises_recall_error():
    with pytest.raises(RecallError):
        RecallMemory.open(command=["/nonexistent/polign", "mcp"])


def test_open_passes_connection_through_env(monkeypatch, tmp_path):
    seen = tmp_path / "env.txt"
    script = tmp_path / "echo_env.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(seen)!r}, 'w').write(json.dumps({{k: os.environ.get(k) for k in "
        "('POLIGN_URL', 'POLIGN_API_KEY', 'POLIGN_COLLECTION', 'POLIGN_PREDICATES')}))\n"
        "for line in sys.stdin:\n"
        "    r = json.loads(line)\n"
        "    if 'id' in r:\n"
        "        res = {'content': [{'type': 'text', 'text': '[]'}]} if r['method'] == 'tools/call' else {}\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': r['id'], 'result': res}), flush=True)\n"
    )
    with RecallMemory.open(
        command=[sys.executable, "-u", str(script)],
        url="http://memory.internal:23000",
        api_key="plgn_test",
        collection="callers",
        predicates="/etc/callers.json",
    ):
        pass
    import json

    assert json.loads(seen.read_text()) == {
        "POLIGN_URL": "http://memory.internal:23000",
        "POLIGN_API_KEY": "plgn_test",
        "POLIGN_COLLECTION": "callers",
        "POLIGN_PREDICATES": "/etc/callers.json",
    }


def test_subject_must_be_set(memory):
    with pytest.raises(ValueError):
        memory.for_subject("  ")
    with pytest.raises(ValueError):
        memory.for_subject("sam", limit=0)


async def test_load_remember_supersede_and_forget(memory):
    sam = memory.for_subject("sam")
    assert await sam.load() == []
    assert sam.loaded and not sam.overflowed

    first = await sam.remember("name", "Sam")
    assert not first.already_known and first.superseded == ()
    again = await sam.remember("name", "Sam")
    assert again.already_known

    changed = await sam.remember("name", "Samantha")
    assert [b.value for b in changed.superseded] == ["Sam"]

    await sam.remember("open_issue", "router drops wifi")
    await sam.remember("open_issue", "billing double charge")
    beliefs = await sam.load()
    assert [(b.predicate, b.value) for b in beliefs] == [
        ("name", "Samantha"),
        ("open_issue", "router drops wifi"),
        ("open_issue", "billing double charge"),
    ]

    hits = await sam.search("wifi keeps dropping")
    assert [b.value for b in hits] == ["router drops wifi"]

    assert await sam.forget("open_issue", "router drops wifi") == 1
    assert await sam.forget("open_issue", all=True) == 1
    assert [b.predicate for b in await sam.load()] == ["name"]

    other = memory.for_subject("other")
    assert await other.load() == []


async def test_overflow_flag_follows_the_cap(memory):
    sam = memory.for_subject("sam", limit=2)
    await sam.remember("open_issue", "a")
    await sam.remember("open_issue", "b")
    await sam.remember("open_issue", "c")
    beliefs = await sam.load()
    assert len(beliefs) == 2 and sam.overflowed


async def test_reads_fail_open_and_writes_raise(memory):
    slow = memory.for_subject("slow", read_timeout=0.2, write_timeout=0.2)
    assert await slow.load() == []
    with pytest.raises(asyncio.TimeoutError):
        await slow.remember("name", "x")
    # The client serializes calls on one subprocess, so the timed-out threads
    # still hold it for a moment; let them drain before the next subject.
    await asyncio.sleep(1.2)

    broken = memory.for_subject("broken")
    assert await broken.load() == []
    with pytest.raises(RecallError):
        await broken.remember("name", "x")


def test_coerce_uses_the_registry_types(memory):
    sam = memory.for_subject("sam")
    assert sam.coerce("name", "  Sam ") == "Sam"
    assert sam.coerce("age", "42") == 42
    assert sam.coerce("age", "42.5") == 42.5
    assert sam.coerce("consents_to_recording", "yes") is True
    assert sam.coerce("consents_to_recording", "false") is False
    for predicate, value in (("age", "old"), ("consents_to_recording", "maybe"), ("name", ""), ("nope", "x")):
        with pytest.raises(ValueError):
            sam.coerce(predicate, value)


async def test_render_groups_multi_values_and_lists_kinds(memory):
    sam = memory.for_subject("sam")
    await sam.remember("name", "Sam")
    await sam.remember("open_issue", "a")
    await sam.remember("open_issue", "b")
    block = render_beliefs(await sam.load(), registry=memory.registry, who="the caller")
    assert block.startswith("<recall_memory>\n") and block.endswith("\n</recall_memory>")
    assert "- name: Sam (observed 2026-09-18)" in block
    assert "- open_issue: a; b" in block
    assert "call the remember tool with it: name, timezone, age, consents_to_recording, open_issue." in block

    empty = render_beliefs([], registry=memory.registry, remember_tool=False)
    assert "Nothing is remembered about the caller yet." in empty
    assert "remember tool" not in empty

    custom = render_beliefs([], template="MEMORY:\n{context}")
    assert custom.startswith("MEMORY:\nNothing is remembered")
    with pytest.raises(ValueError):
        render_beliefs([], template="no placeholder")


def test_local_dir_is_handed_to_the_client(monkeypatch, tmp_path):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def predicates(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr("recall_livekit.memory.Client", FakeClient)
    with RecallMemory.open(local_dir=tmp_path / "recall-data", predicates="/etc/callers.json"):
        pass
    assert seen["local_dir"] == tmp_path / "recall-data"
    assert seen["env"] == {"POLIGN_PREDICATES": "/etc/callers.json"}


def test_local_dir_excludes_a_remote_connection(tmp_path):
    with pytest.raises(ValueError):
        RecallMemory.open(local_dir=tmp_path, url="http://memory.internal:23000")
