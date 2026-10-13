# Recall Helm chart 0.2.0

Requires recall 0.17.0 or newer, the default image.

- `kubernetes.sandboxClaims` passes `-kubernetes-sandbox-claims` (recall
  0.17.0 or newer): with agent-sandbox, the agent is the name of the
  SandboxClaim that owns the pod's Sandbox. The ClusterRole then also gets
  `get` on Pods, `sandboxes.agents.x-k8s.io` and
  `sandboxclaims.extensions.agents.x-k8s.io`.

# Recall Helm chart 0.1.0

Requires recall 0.16.0 or newer, the release that adds `recall serve`.

- First chart. `recall serve` against a polign_db or Qdrant store the operator
  already runs, with the store's key from a Secret. Stored Recall keys with
  expiry and tombstone revocation; `auth.bootstrapKey` registers an admin key at
  start so keys can be minted over HTTP; `auth.keysFile` adds static keys.
- `kubernetes.enabled` turns on workload token exchange: pods trade a projected
  service account token (audience `recall`) for a short-lived key scoped to
  `<tenant>:<pod>`. The chart mounts the server's own token and creates the
  ClusterRole for TokenReviews, plus ServiceAccount reads when
  `kubernetes.tenantLabel` is set.
- The deployment refuses to render when nobody could call the server.
