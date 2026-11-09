# The Recall memory provider on Hermes's provider interface, for an Agent
# Substrate actor. run-kind.sh assembles the build context: the plugin from
# this repository and four modules from a hermes-agent checkout.

FROM python:3.13-slim
RUN pip install --no-cache-dir "polign-recall>=0.24.0,<1"
WORKDIR /app
# From hermes-agent: the MemoryProvider base class and the secret scope the
# plugin imports.
COPY hermes/agent/__init__.py hermes/agent/jiter_preload.py hermes/agent/memory_provider.py hermes/agent/secret_scope.py agent/
COPY hermes/hermes_constants.py .
# The plugin, unchanged.
COPY plugin/__init__.py recall_plugin/__init__.py
COPY driver.py .
ENV PYTHONUNBUFFERED=1 HERMES_HOME=/app/home
RUN mkdir -p /app/home
EXPOSE 80
ENTRYPOINT ["python", "driver.py"]
