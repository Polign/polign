# Build from the polign repository root:
# docker build -f examples/vapi/Dockerfile -t recall-vapi .
FROM python:3.12-slim
WORKDIR /app
COPY python/recall-vapi /tmp/recall-vapi
RUN pip install --no-cache-dir '/tmp/recall-vapi[fastapi]' 'uvicorn>=0.30,<1'
COPY examples/vapi/app.py ./app.py
ENV RECALL_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
