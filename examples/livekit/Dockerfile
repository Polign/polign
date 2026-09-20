# A LiveKit agent worker with Recall memory.
#
# pip installs everything, including the `polign` CLI that Recall runs (it
# comes with recall-livekit as the polign_db package).
FROM python:3.12-slim

# LiveKit agents need a writable home for model downloads (Silero VAD).
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/appuser
RUN useradd -m -u 1000 appuser
WORKDIR /home/appuser/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py ./
RUN chown -R appuser:appuser /home/appuser
USER appuser

# Download model weights into the image so a cold start does not fetch them.
RUN python agent.py download-files

CMD ["python", "agent.py", "start"]
