# A LiveKit agent worker with Recall memory.
#
# The `polign` CLI comes from the published server image, so the worker never
# downloads anything at build time beyond pip packages. Pin both versions.
ARG POLIGN_VERSION=0.6.6
FROM ghcr.io/polign/polign-server:${POLIGN_VERSION} AS polign

FROM python:3.12-slim

# LiveKit agents need a writable home for model downloads (Silero VAD).
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/appuser
RUN useradd -m -u 1000 appuser
WORKDIR /home/appuser/app

COPY --from=polign /usr/local/bin/polign /usr/local/bin/polign

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py ./
RUN chown -R appuser:appuser /home/appuser
USER appuser

# Download model weights into the image so a cold start does not fetch them.
RUN python agent.py download-files

CMD ["python", "agent.py", "start"]
