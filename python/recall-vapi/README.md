# recall-vapi

Caller memory for Vapi voice assistants, backed by Recall by Polign. Loads
typed facts before the conversation, saves corrections through a `remember`
Function tool, and remembers them on the next call. Uses the existing
`polign-recall` client and the same starter predicates as `recall-livekit`;
LiveKit is not a dependency. No separate extraction model runs.

This package is under development in this repository, not yet published.

```bash
pip install -e './python/recall-vapi[fastapi]'
```

## Quick start

See [the runnable FastAPI example](../../examples/vapi/README.md) for inbound
phone setup, authentication, a Docker image, and a local two-call check.

```python
from recall_vapi import RecallMemory, RecallVapi, SQLiteState
from recall_vapi.fastapi import create_router

# Once per worker, during application startup:
memory = RecallMemory.open(local_dir="./data/memory", timeout=4)


async def resolve_subject(message):
    # Resolve through your own customer directory. Return None for anonymous
    # callers. Do not use an LLM argument or untrusted browser metadata here.
    return await your_customer_directory.resolve(message["call"])


adapter = RecallVapi(
    memory=memory,
    state=SQLiteState("./data/vapi.sqlite3"),
    assistant=your_base_assistant_configuration,
    tool_server={
        "url": "https://your-service.example/vapi/webhook",
        "headers": {"Authorization": "Bearer " + webhook_token},
    },
    resolve_subject=resolve_subject,
)
app.include_router(create_router(adapter, token=webhook_token))
# During shutdown: await adapter.aclose()
```

`your_customer_directory`, the base assistant, token, and FastAPI application
above are supplied by your application. The example supplies all of them.
Use `env={"POLIGN_URL": "https://...", "POLIGN_API_KEY": "..."}` instead of
`local_dir` for a shared Polign server. S3/GCS configuration belongs on that
server, exactly as in the LiveKit integration. Pass `predicates="callers.json"`
to use your own closed predicate registry.

## Vapi configuration

For inbound calls, leave the phone number's assistant unassigned and set its
server URL to this webhook. Configure a bearer credential on that saved phone
number. Vapi sends `assistant-request`; the adapter binds the call ID to the
resolved subject and returns a transient assistant containing its memory.
The saved base assistant is never edited. Vapi requires an assistant response
within 7.5 seconds; the router has a six-second handling budget, including
bounded resolver and memory reads. Network time is additional. Deploy nearby
and measure call setup latency.

The generated tools have an explicit `server.headers.Authorization`, because
Vapi may withhold organization credentials for URLs supplied in a transient
configuration. The example also puts this header on the assistant's server
for end reports. Keep these configurations on your backend. For browser
integrations, register authenticated tools as saved Vapi resources and use
their `model.toolIds` instead of delivering inline credentials to the browser.

Official contracts: [server events](https://docs.vapi.ai/server-url/events),
[Function tools](https://docs.vapi.ai/tools/custom-tools), and
[server authentication](https://docs.vapi.ai/server-url/server-authentication).

## Tools and call lifecycle

* `remember(predicate, value)` stores or corrects a caller-stated fact. Schemas
  come from the registry; values are converted to its string/number/boolean
  type and validated again by Recall. The result includes superseded values.
* `recall(query)` searches only the bound caller's memory. Initial loading is
  capped at 20 facts by default. This is model-triggered search, not LiveKit's
  automatic per-turn overflow hook.
* `forget(predicate, value)` is opt-in with `forget_tool=True`. It withdraws a
  specific current fact. History remains; this is not complete data erasure.

Tool arguments cannot choose a customer. Unknown calls, changed bindings,
extra arguments, and disabled tools are rejected. An unresolved customer gets
the base assistant with no memory tools. A failed initial read continues with
an explicit unavailable-memory block. Successful tool results take precedence
over the initial prompt; the adapter does not rewrite the live system prompt.

End-of-call reports close bindings but retain duplicate results. Other server
events are acknowledged and ignored; transcripts are not stored or extracted.
Set the assistant's server URL to this webhook and include `end-of-call-report`
in `serverMessages` so bindings close. The example does this.

## Outbound and web calls

Your authenticated backend can call `await adapter.prepare_assistant(subject)`
before creating a call, then `adapter.bind_call(call_id, subject)` with the ID
Vapi returns. Bind promptly before tools execute: an early tool webhook is
rejected until the binding exists. For an outbound production flow that cannot
tolerate this race, supply a trusted customer resolver backed by a pre-created
call record in your own integration. The included example targets inbound calls.

For web calls, never return the example's transient configuration (it contains
the webhook credential) to a browser. Create the call on your backend, or
replace inline tools with saved tool IDs before passing configuration to the
client. See [Vapi web calls](https://docs.vapi.ai/quickstart/web).

## Retries, concurrency, and persistence

`SQLiteState` stores call bindings, normalized tool requests, and results.
Concurrent duplicate deliveries reserve only one execution. A delayed retry
of an old correction returns its old result without changing the newer fact.
Reusing an ID with different arguments is rejected. Writes that time out can
still finish; their eventual result is recorded for subsequent retries.

This provides at-most-once execution for each `(call_id, tool_call_id)` while
the ledger is retained, not atomic exactly-once delivery across SQLite and
Recall. A crash after reservation may leave a pending operation, whether or
not Recall committed it. Such operations are never automatically replayed;
reconcile the ledger's normalized request against Recall history. Successful
remember results include the event ID; the Recall source is `user_stated`.
A new tool-call ID is a new operation.

Keep the SQLite file on persistent local disk. Multiple workers on one host
may share it; separate machines need an equivalent shared transactional state
implementation with `bind`, `subject`, `reserve`, `finish_tool`, and
`finish_call` methods. Do not use independent ledgers or put SQLite on NFS.
State is retained until you remove it; include its customer IDs and results
in your retention policy. Removing it also removes deduplication protection
and bindings for old calls. Back it up alongside the memory store.

The Recall client serializes requests. Bounded worker slots and timeouts keep
slow operations from accumulating without limit. Defaults are one second for
identity resolution and reads, three seconds for tool writes, and eight
outstanding operations per adapter. Configure the client's transport timeout
as well. `aclose()` waits for pending work before closing that client.

## Development

```bash
cd python/recall-vapi
pip install -e '.[dev]' build
pytest tests/unit_tests -q
pytest tests/integration_tests -q  # real binaries from polign_db (no Vapi key)
python -m build
```

Tests cover Vapi payload variants, identity isolation, corrections across
calls, duplicate delivery and restart, unknown outcomes, and HTTP credentials.
Integration tests run the real Recall subprocess and an isolated Polign server.
They do not exercise Vapi's live voice pipeline. The example README describes
that final manual test.

The voice predicate JSON is copied from `recall-livekit` to avoid a dependency
on a voice runtime. A test checks parity when both source packages are present.
CI includes this package; `vapi/v0.1.0` is its publishing tag. Before publishing,
register the `recall-vapi` PyPI trusted publisher for this repository's
`python-publish.yml` workflow and `pypi` environment.
