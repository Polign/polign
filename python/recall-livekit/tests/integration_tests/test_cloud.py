"""Identical Recall behavior against isolated S3 and GCS emulators."""

import secrets
import urllib.error
import urllib.request

import pytest

from recall_livekit import VOICE_REGISTRY, RecallMemory

from .conftest import _locate, running_server


async def test_authenticated_cloud_memory_survives_server_restart(tmp_path, cloud_store):
    server, cli = _locate(tmp_path)
    store, env, list_keys = cloud_store
    key = f"plgn_{secrets.token_hex(8)}_{secrets.token_hex(32)}"
    key_file = tmp_path / "api-key"
    key_file.write_text(key)
    key_file.chmod(0o600)
    flags = [
        "-require-data-key", "-bootstrap-key-file", str(key_file),
        "-disk-cache-bytes", "0", "-maintain", "0",
    ]

    def memory(url):
        return RecallMemory.open(
            command=[str(cli), "mcp", "-memory-only", "-write"],
            url=url, api_key=key, collection="callers", predicates=VOICE_REGISTRY,
        )

    with running_server(server, store, env=env, flags=flags) as url:
        with memory(url) as recall:
            caller = recall.for_subject("cloud-test-caller", read_timeout=10, write_timeout=20)
            await caller.remember("name", "Sam")
            changed = await caller.remember("name", "Samantha")
            assert [b.value for b in changed.superseded] == ["Sam"]
            await caller.remember("open_issue", "router drops wifi")
            assert {(b.predicate, b.value) for b in await caller.load()} == {
                ("name", "Samantha"), ("open_issue", "router drops wifi"),
            }
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(f"{url}/v1/collections/callers/describe", timeout=5)
        assert denied.value.code == 401

    keys = list_keys()
    assert any("/.auth/" in key for key in keys)
    assert any("/.auth/" not in key for key in keys)

    # A fresh process with no local disk cache must recover from the bucket.
    with running_server(server, store, env=env, flags=flags) as url:
        with memory(url) as recall:
            caller = recall.for_subject("cloud-test-caller", read_timeout=10, write_timeout=20)
            assert {(b.predicate, b.value) for b in await caller.load()} == {
                ("name", "Samantha"), ("open_issue", "router drops wifi"),
            }
            assert any(b.value == "router drops wifi" for b in await caller.search("wifi router"))
            assert await caller.forget("open_issue", "router drops wifi") == 1
            assert [(b.predicate, b.value) for b in await caller.load()] == [("name", "Samantha")]

    with running_server(server, store, env=env, flags=flags) as url:
        with memory(url) as recall:
            caller = recall.for_subject("cloud-test-caller", read_timeout=10)
            assert [(b.predicate, b.value) for b in await caller.load()] == [("name", "Samantha")]
            other = recall.for_subject("different-caller", read_timeout=10)
            assert await other.load() == []
            assert other.loaded
