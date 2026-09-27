FROM docker.io/library/python:3.12-slim
RUN pip install --no-cache-dir "recall-langgraph==0.1.1" "polign_db==0.8.1" "langchain-openai>=1.0,<2"
WORKDIR /app
COPY agent.py task.py ./
ARG AGENT_VERSION=v1
ARG MODEL=gpt-4.1-mini
ENV AGENT_VERSION=$AGENT_VERSION MODEL=$MODEL PYTHONUNBUFFERED=1
CMD ["python", "agent.py"]
