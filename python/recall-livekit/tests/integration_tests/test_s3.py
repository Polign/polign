"""Real Recall and Polign binaries against an isolated S3-compatible emulator.

Install ``moto[server]`` to enable this test. No AWS account is used.
"""

import os
import secrets
import urllib.error
import urllib.request

import pytest

from recall_livekit import VOICE_REGISTRY, RecallMemory

from .conftest import _locate, running_server


async def test_authenticated_s3_memory_survives_server_restart(tmp_path):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto.server")
    server, cli = _locate(tmp_path)
    emulator = moto.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    emulator.start()
    try:
        host, port = emulator.get_host_and_port()
        endpoint = f"http://{host}:{port}"
        s3 = boto3.client(
            "s3", endpoint_url=endpoint, region_name="us-east-1",
            aws_access_key_id="testing", aws_secret_access_key="testing",
        )
        bucket, prefix = "recall-livekit-test", "memory"
        s3.create_bucket(Bucket=bucket)
        # Exclude inherited store credentials, endpoints, or encryption settings.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "POLIGN_"))}
        env.update(
            AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
            AWS_REGION="us-east-1", AWS_ENDPOINT_URL_S3=endpoint,
            AWS_S3_FORCE_PATH_STYLE="true", AWS_EC2_METADATA_DISABLED="true",
            AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
        )
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

        with running_server(server, f"s3://{bucket}/{prefix}", env=env, flags=flags) as url:
            with memory(url) as recall:
                caller = recall.for_subject("s3-test-caller", read_timeout=10, write_timeout=20)
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

        objects = s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "/")["Contents"]
        assert any("/.auth/" in obj["Key"] for obj in objects)
        assert any("/.auth/" not in obj["Key"] for obj in objects)

        # A fresh process with no local disk cache must recover from the bucket.
        with running_server(server, f"s3://{bucket}/{prefix}", env=env, flags=flags) as url:
            with memory(url) as recall:
                caller = recall.for_subject("s3-test-caller", read_timeout=10, write_timeout=20)
                assert {(b.predicate, b.value) for b in await caller.load()} == {
                    ("name", "Samantha"), ("open_issue", "router drops wifi"),
                }
                assert any(b.value == "router drops wifi" for b in await caller.search("wifi router"))
                assert await caller.forget("open_issue", "router drops wifi") == 1
                assert [(b.predicate, b.value) for b in await caller.load()] == [("name", "Samantha")]
    finally:
        emulator.stop()
