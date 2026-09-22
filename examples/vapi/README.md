# Vapi voice assistant with Recall memory

A runnable inbound phone assistant that remembers callers, corrects facts
during the conversation, and loads them on the next call. Uses the
[recall-vapi adapter](../../python/recall-vapi/README.md).

## Guided live phone test

Use Python 3.10 or newer, a Vapi phone number, Vapi credits/provider access,
and an installed `cloudflared` for the temporary HTTPS tunnel. Start from this
directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python live_test.py init --phone-number-id YOUR_VAPI_PHONE_NUMBER_ID
python live_test.py serve --tunnel
```

Use the phone number's resource ID from Vapi, not its dialable number.
`init` creates `.env` with two independent secrets and preserves existing
settings. Use a Vapi number dedicated to this test. `serve --tunnel` uses an
installed `cloudflared` to create a temporary public HTTPS URL, writes that
URL to `.env`, starts the app, and keeps both processes running. The URL changes
on each tunnel launch; update Vapi when it does. If you exported an older
`PUBLIC_BASE_URL` in your shell, unset it before running the other commands.
An existing HTTPS reverse proxy can be used with `serve` without `--tunnel`;
set `PUBLIC_BASE_URL` in `.env` first.

The helper isolates Cloudflare's configuration from existing named tunnels.
If the local DNS resolver cannot resolve a generated `trycloudflare.com` hostname,
the HTTPS check resolves that hostname through Google's public DNS-over-HTTPS
endpoint while retaining normal TLS hostname and certificate verification.
This fallback applies only to temporary Quick Tunnel hosts and changes no
system DNS settings. Other connection failures are reported normally.

In a second terminal, in this directory with the same virtual environment:

```bash
python live_test.py check
python live_test.py configure
```

`check` verifies public HTTPS, rejected unauthenticated requests, and synthetic
memory calls. `configure` writes the private phone-server settings to
`data/live-test/phone-server.json` and prints the Vapi dashboard steps. It does
not modify your Vapi account. The detailed dashboard instructions are below.
You do not need to give this service your Vapi private API key.

After configuring the number, make two calls from the same phone:

1. Say “My name is Alex.” Wait for the reply, then say “Actually, call me Sam.”
   Wait for the reply and hang up.
2. Call again, listen for a greeting using Sam, and hang up.

Inspect the observed call IDs and evaluate that pair:

```bash
python live_test.py report
python live_test.py report --first-call CALL_1_ID --second-call CALL_2_ID
```

The report checks that the calls resolved to the same caller, Alex was saved
before the correction to Sam, the second call's initial context contained Sam,
both calls ended, and no tool errors were observed. It saves the result in
`data/live-test/report.json`. This verifies webhooks and memory; confirm the
actual spoken greeting by listening. A pending test never reports success,
and synthetic `verify-*` calls are excluded.

Live test mode records only call IDs, hashed caller keys, name-memory evidence,
tool error counts, and ended reasons in a private `events.jsonl` file. It does
not record audio, transcripts, or complete webhook payloads. The name evidence
is still personal data; keep this directory private. Ordinary `uvicorn app:app`
only enables that recorder when `VAPI_LIVE_TEST=1` is set.

Ctrl-C stops the app and tunnel. Memory data and test evidence remain locally;
the managed Recall database process continues across app restarts. Restore the
phone number's prior routing in Vapi before closing the tunnel if it was not a
dedicated test number. [Cloudflare Quick Tunnels documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).

## Run locally without the test helper

From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
test -f .env || cp .env.example .env
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

Retain the `data/` directory across restarts; it holds the memory store.
To use an existing Polign server instead, set `POLIGN_URL`, `POLIGN_API_KEY`,
and optionally `POLIGN_COLLECTION`. Recall is the only store: call bindings
and the retry ledger are records in a `vapi_calls` collection on the same
server, so several workers or hosts pointed at it share them.

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
leave pending records that require manual reconciliation. See the
[adapter README](../../python/recall-vapi/README.md#retries-concurrency-and-persistence).
