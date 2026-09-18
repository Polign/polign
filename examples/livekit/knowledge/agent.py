"""A LiveKit voice agent that answers from a polign_db knowledge base.

This is the retrieval side, not memory: product documents are indexed into a
collection once, and every user turn looks up the closest passages and adds
them to that turn before the model answers. Recall memory (see ../agent.py)
and this can be combined in one agent.

    pip install -r requirements.txt
    python index.py docs/          # index a folder of .md or .txt files once
    python agent.py console

Keyword (BM25) search needs no embedding model and is what runs below. Pass
``values=`` from your embedder as well for hybrid search.
"""

import asyncio
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, cli, llm
from livekit.plugins import cartesia, deepgram, openai, silero
from polign import Client

load_dotenv()

COLLECTION = os.environ.get("POLIGN_COLLECTION", "acme_docs")
server = AgentServer()


def setup(proc: JobProcess) -> None:
    proc.userdata["polign"] = Client(
        os.environ.get("POLIGN_URL", "http://localhost:23000"),
        api_key=os.environ.get("POLIGN_API_KEY"),
        timeout=5.0,
    )
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = setup


class DocsAgent(Agent):
    def __init__(self, client: Client) -> None:
        super().__init__(
            instructions="You answer questions about Acme Home Internet from the passages "
            "given to you. If the passages do not cover the question, say so."
        )
        self._client = client

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        question = new_message.text_content
        if not question:
            return
        try:
            hits = await asyncio.wait_for(
                asyncio.to_thread(self._client.search, COLLECTION, text=question, k=3), 0.8
            )
        except Exception:  # the answer degrades, the call does not drop
            return
        passages = [str(h.metadata.get("text", "")) for h in hits if h.metadata]
        if passages:
            turn_ctx.add_message(
                role="system",
                content="Passages from the product documentation relevant to the caller's "
                "question:\n\n" + "\n\n".join(passages),
            )


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=cartesia.TTS(),
        vad=ctx.proc.userdata["vad"],
    )
    await session.start(agent=DocsAgent(ctx.proc.userdata["polign"]), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
