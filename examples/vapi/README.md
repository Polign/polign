# Vapi voice assistant with Recall memory

A runnable inbound phone assistant that remembers callers, corrects facts
during the conversation, and loads them on the next call. Uses the
[recall-vapi adapter](../../python/recall-vapi/README.md).

## Run locally

From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`: set your Vapi phone number ID, tenant name, public HTTPS endpoint,
and two independent random secrets. Generate each secret with:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Keep `CALLER_IDENTITY_KEY` stable: changing it changes every derived caller ID.
Then load your own `.env` and start the server:

```bash
set -a
source .env
set +a
uvicorn app:app --host 127.0.0.1 --port 8000
```

Expose port 8000 through your HTTPS tunnel or deployment. Set `PUBLIC_BASE_URL`
to that public origin and restart the app if it changes. The memory tools use
that URL. `/healthz` is public; `/vapi/webhook` requires bearer authentication.
The database is created automatically in `./data/memory`.

In another terminal with the environment loaded, run:

```bash
python verify_memory.py
```

This calls the local webhook with synthetic Vapi messages. It stores Alex,
corrects the name to Sam, retries the old write, and checks a second call loads
Sam. It needs no Vapi API key and makes no phone calls. It leaves synthetic
facts for the fictional number `+12025550199`; do not use this number for real
customer records. To target your deployment, set `VERIFY_BASE_URL` to its origin.

## Connect Vapi

1. In Vapi, use a phone number in your account. Its ID must match
   `VAPI_PHONE_NUMBER_ID` in the app environment.
2. Under Integrations → Server Configuration, create a Custom Credential of
   type Bearer Token. Set its token to `VAPI_WEBHOOK_TOKEN`, header name to
   `Authorization`, and enable the Bearer prefix.
3. Configure the **phone number's** server URL as
   `https://your-host/vapi/webhook` and select that credential. Leave the phone
   number's assistant unassigned so Vapi requests one from the service.
4. Call the number. Say “My name is Alex,” then “Actually, call me Sam.” End the
   call and call again. The assistant should greet you as Sam. Ask it to
   forget that name to exercise withdrawal.

The service returns a transient assistant with its memory tools and server
authentication already configured. It uses a model-generated greeting so
remembered facts are available before the first sentence. Change the model,
voice, and base instructions in `app.py` to match your Vapi provider setup.
Vapi credits/provider access are needed for actual calls. No Vapi private API
key is needed by this webhook service; it responds to Vapi requests.

Official setup references: [server URLs](https://docs.vapi.ai/server-url/setting-server-urls),
[authentication](https://docs.vapi.ai/server-url/server-authentication), and
[assistant-request events](https://docs.vapi.ai/server-url/events).

## Identity and persistence

This example identifies an inbound caller using an HMAC of your tenant and
their E.164 phone number. Hidden/invalid numbers and unexpected phone number
IDs get an anonymous assistant. Caller ID is suitable only for demonstrating
low-sensitivity preferences. For account-specific memory, replace
`resolve_subject` with your authenticated customer lookup. Webhook credentials
authenticate Vapi, not the person on the phone.

For one host, retain the whole `data/` directory across restarts. It contains
the memory store plus `vapi.sqlite3`, the durable bindings and retry ledger.
To use an existing Polign server instead, set `POLIGN_URL`, `POLIGN_API_KEY`,
and optionally `POLIGN_COLLECTION`. Keep the SQLite ledger persistent even
when the memory store is remote. Multiple hosts need shared transactional
call state; the included SQLite implementation is for one host.

The local memory server started by Recall keeps running across app restarts.
Its process ID and log are in `data/memory/runtime.json` and `server.log`.

## Docker

Build from the repository root:

```bash
docker build -f examples/vapi/Dockerfile -t recall-vapi .
docker run --rm --env-file examples/vapi/.env \
  -e RECALL_DATA_DIR=/data -v recall-vapi-data:/data \
  -p 127.0.0.1:8000:8000 recall-vapi
```

Put an HTTPS reverse proxy in front of the container. Keep the named volume
on redeploys. For production deployments, the separate shared Polign server
option makes memory-server lifecycle management explicit.

## Limits

Initial facts are loaded once; `recall` supplies further facts when the model
asks. `remember` returns corrections immediately but does not replace the
live system prompt. No passive transcript extractor is enabled. `forget`
withdraws a current fact while retaining history.

Writes may finish after an HTTP timeout. The ledger prevents replaying the
same tool-call ID and reports unknown outcomes honestly. A service crash can
leave pending reservations that require manual reconciliation. See the
[adapter README](../../python/recall-vapi/README.md#retries-concurrency-and-persistence).
