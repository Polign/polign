"""A LiveKit voice agent that remembers the caller with Recall by Polign.

Run it locally against a LiveKit project:

    pip install -r requirements.txt
    cp .env.example .env    # fill in the LiveKit, model, and Polign settings
    python agent.py console # talk to it in the terminal
    python agent.py dev     # connect it to your LiveKit project

The caller's facts are loaded before the greeting, the model stores new ones
through the ``remember`` tool, and the next call from the same identity starts
with everything in place.
"""

import logging
import os

from dotenv import load_dotenv
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli
from livekit.plugins import cartesia, deepgram, openai, silero
from recall_livekit import VOICE_REGISTRY, RecallAgent, RecallMemory

load_dotenv()
logger = logging.getLogger("acme-support")

INSTRUCTIONS = """You are the phone support line for Acme Home Internet.
Keep replies short and spoken; no lists or markdown.
Use what you remember about the caller: greet them by name when you know it,
and pick up open issues without making them repeat the story.
When the caller tells you a lasting fact (their name, a callback number,
a new open issue, a preference), call the remember tool right away."""

server = AgentServer()


def setup(proc: JobProcess) -> None:
    """One Recall subprocess per worker process. It reads POLIGN_URL,
    POLIGN_API_KEY and POLIGN_COLLECTION from the environment."""
    proc.userdata["recall"] = RecallMemory.open(
        predicates=os.environ.get("POLIGN_PREDICATES", VOICE_REGISTRY),
    )
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = setup


class SupportAgent(RecallAgent):
    async def on_enter(self) -> None:
        await super().on_enter()  # loads the caller's memory into the instructions
        self.session.generate_reply(
            instructions="Greet the caller. If you know their name, use it. "
            "If they have an open issue, ask whether it is still a problem."
        )


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    participant = await ctx.wait_for_participant()
    # The participant identity comes from the token your server issued, so it
    # is a stable, auth-derived id. Never key memory on the room name.
    memory: RecallMemory = ctx.proc.userdata["recall"]
    caller = memory.for_subject(participant.identity)
    logger.info("session for %s", participant.identity)

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=cartesia.TTS(),
        vad=ctx.proc.userdata["vad"],
    )
    await session.start(
        agent=SupportAgent(memory=caller, instructions=INSTRUCTIONS, forget_tool=True),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(server)
