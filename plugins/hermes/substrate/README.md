# The Recall provider for Hermes on Agent Substrate

On [Agent Substrate](https://github.com/agent-substrate/substrate) a Hermes
agent runs as an actor. Substrate snapshots the actor when it is idle, and the
snapshot ends when the actor is deleted. With this plugin pointed at a Recall
server, what the agent learned lives outside the actor, and the actor holds no
credential for it.

The plugin needs no change. Two settings in the actor's environment:

```yaml
env:
- name: RECALL_URL
  value: http://recall.recall-system.svc.cluster.local
- name: RECALL_API_KEY
  value: placeholder
```

The plugin sends the placeholder as its bearer. The actor's egress policy
(`egress-policy.yaml`) has Substrate's gateway replace it with a JWT minted for
that actor, and a Recall server started with `-substrate-issuer` accepts that
token and gives the actor its own memory. The server side is in
[deploy/agent-substrate](https://github.com/Polign/recall/tree/main/deploy/agent-substrate)
in Polign/recall.

## Run it on kind

Run Recall's Substrate example first, so the cluster has a Recall server and a
worker pool. Then, with a hermes-agent checkout:

```sh
HERMES_AGENT_DIR=~/code/hermes-agent ./run-kind.sh
```

The script checks five things:

1. A completed turn is sent to Recall.
2. The next turn starts with that memory in front of it.
3. The agent's tools (`polign_recall_search`) reach the same memory.
4. After a suspend and resume, the provider saves and recalls without being
   reopened.
5. After the actor is deleted and created again, a new process starts with the
   old memory.

## What this does and does not run

The actor runs the plugin as shipped, on the `MemoryProvider` base class and
secret scope copied from the hermes-agent checkout. `driver.py` stands in for
the agent loop and calls the hooks Hermes calls around a turn, so the example
needs no model and no model key. It does not run the Hermes agent itself.

The Recall server in the example has no extraction model, so each turn is kept
as written and found by its words.

Tested on kind with Substrate v1.0.0, polign-recall 0.24.0, and hermes-agent
at 4f6ebb9afe.
