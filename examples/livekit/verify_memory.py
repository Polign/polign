"""Verify remote Recall storage without a LiveKit room or model provider.

Writes a synthetic caller in a separate collection. Use --read-only with the
printed subject after restarting the server to check recovery from its store.
"""

import argparse
import asyncio
import os
import uuid

from dotenv import load_dotenv
from recall_livekit import VOICE_REGISTRY, RecallMemory


async def verify(args):
    connection = dict(
        url=os.environ["POLIGN_URL"],
        api_key=os.environ.get("POLIGN_API_KEY"),
        collection=args.collection,
        predicates=VOICE_REGISTRY,
    )
    if not args.read_only:
        with RecallMemory.open(**connection) as memory:
            caller = memory.for_subject(args.subject, read_timeout=10, write_timeout=30)
            await caller.remember("name", "Setup Sam")
            changed = await caller.remember("name", "Setup Samantha")
            if not any(b.value == "Setup Sam" for b in changed.superseded):
                raise RuntimeError("Name correction did not supersede the previous fact")

    # A new MCP process must see the saved fact, with no client memory to reuse.
    with RecallMemory.open(**connection, write=False) as memory:
        caller = memory.for_subject(args.subject, read_timeout=10)
        beliefs = await caller.load()
        if not caller.loaded or [(b.predicate, b.value) for b in beliefs] != [
            ("name", "Setup Samantha")
        ]:
            raise RuntimeError("The server did not return the expected saved fact")
    print(f"PASS: collection={args.collection} subject={args.subject}")
    print("Restart the Polign server, then verify again using:")
    print(f"python verify_memory.py --read-only --collection {args.collection} --subject {args.subject}")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="callers_setup_check")
    parser.add_argument("--subject")
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("POLIGN_URL", "").strip():
        parser.error("set POLIGN_URL to the HTTP(S) URL of your Polign server")
    if args.read_only and not args.subject:
        parser.error("--read-only requires the --subject printed by an earlier check")
    args.subject = args.subject or "setup-" + uuid.uuid4().hex
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
