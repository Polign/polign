# Recall on Kubernetes

This chart runs `recall serve`, Recall's front door: the memory tools over HTTP
and MCP for many agents at once, each inside the namespace its key names, on
a vector store you already run. The server holds the store's credentials; the
agents hold Recall keys, or nothing but their pod identity.

The store is a separate install. The recommended one is polign_db through the
`polign` chart in this repository: bucket-native storage, leases for the
resume tools, lexical search and cached reads. Qdrant works for the memory
tools alone. The capability matrix is in the
[Memory API](https://github.com/Polign/recall/blob/main/docs/memory-api-v1.md).

## Install with polign_db

```sh
# 1. The store (see charts/polign). A bootstrap key lets recall serve in.
kubectl create namespace polign
kubectl -n polign create secret generic polign-key --from-literal=api-key="$(polign-apikey generate)"
helm install polign oci://ghcr.io/polign/charts/polign -n polign \
  --set store.uri=s3://my-bucket/polign --set auth.existingSecret=polign-key

# 2. An admin key for recall serve, any rcl_<16 hex>_<secret> you generate.
kubectl create namespace recall
kubectl -n recall create secret generic recall-admin --from-literal=key="rcl_$(openssl rand -hex 8)_$(openssl rand -base64 24 | tr '+/' '-_' | tr -d =)"
kubectl -n recall create secret generic polign-key --from-literal=api-key="$(kubectl -n polign get secret polign-key -o jsonpath='{.data.api-key}' | base64 -d)"

# 3. recall serve.
helm install recall oci://ghcr.io/polign/charts/recall -n recall \
  --set backend.url=http://polign.polign.svc:23000 \
  --set backend.existingSecret=polign-key \
  --set auth.bootstrapKey.existingSecret=recall-admin
```

The admin key reaches no memory; it mints keys. `helm status recall` prints
the command that mints the first one.

## Workload identity

With `kubernetes.enabled=true` a pod needs no key at all. It projects a
service account token with audience `recall` and posts it to
`/v1/auth/kubernetes`; the server verifies it with the API server and mints a
15 minute key for `<tenant>:<pod name>`, fixed to that agent, which the pod
renews. The tenant is the pod's service account namespace, or a label on the
service account named by `kubernetes.tenantLabel`. Nothing a tenant controls
is consulted, so a pod can only ever act as itself.

The chart creates the ClusterRole the server needs (create TokenReviews, and
get ServiceAccounts when `tenantLabel` is set). The agent-sandbox demo in
[Polign/recall `deploy/agent-sandbox`](https://github.com/Polign/recall/tree/main/deploy/agent-sandbox)
uses this mode.

## Limitations

- A revoked or expired key is still accepted for `auth.cacheTTL` (30 seconds).
- A deleted pod's key stays valid until its TTL (15 minutes) unless revoked
  through `/v1/keys`.
- Keys cannot be rotated in flight; a workload exchanges a new one.
- The resume tools need a backend with leases: polign_db, not Qdrant.
