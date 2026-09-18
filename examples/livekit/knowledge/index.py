"""Index a folder of text files into a polign_db collection for keyword search.

    python index.py docs/

Each file becomes one record per paragraph. The text lives in metadata under
``text``, which is what BM25 search and the agent read. No embedding model is
involved; a one-dimensional placeholder vector satisfies the schema.
"""

import os
import pathlib
import sys
import uuid

from polign import Client

COLLECTION = os.environ.get("POLIGN_COLLECTION", "acme_docs")


def paragraphs(path: pathlib.Path):
    for chunk in path.read_text(encoding="utf-8").split("\n\n"):
        chunk = " ".join(chunk.split())
        if len(chunk) > 40:
            yield chunk


def main(folder: str) -> None:
    client = Client(os.environ.get("POLIGN_URL", "http://localhost:23000"), api_key=os.environ.get("POLIGN_API_KEY"))
    records = []
    for path in sorted(pathlib.Path(folder).rglob("*")):
        if path.suffix.lower() not in (".md", ".txt"):
            continue
        for text in paragraphs(path):
            records.append({"id": uuid.uuid5(uuid.NAMESPACE_URL, f"{path}:{text[:80]}").hex,
                            "values": [0.0], "metadata": {"text": text, "source": path.name}})
    for start in range(0, len(records), 1000):
        client.put_many(COLLECTION, records[start:start + 1000])
    print(f"indexed {len(records)} passages into {COLLECTION}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs")
