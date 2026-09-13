# Polign on Kubernetes

Run Polign against your own bucket. The chart deploys one server with embedded
persistence and maintenance; Recall and other clients connect to its HTTP API.
Requires Polign 0.6.6+, Helm 3.22+, and Kubernetes 1.30+.
The recovery suite runs on Kubernetes 1.35.8.

## Try it

You need `kubectl` pointed at a cluster and Helm 3.22+, and nothing else: no
bucket, no cloud identity, and no Polign tooling installed. A key is a prefix
and two random values, so any source of randomness makes one.

```sh
kubectl create namespace polign
kubectl -n polign create secret generic polign-key \
  --from-literal=api-key="plgn_$(openssl rand -hex 8)_$(openssl rand -hex 32)"
helm install polign oci://ghcr.io/polign/charts/polign --version 0.2.0 \
  --namespace polign --set store.claim.create=true --set auth.existingSecret=polign-key --wait
```

`store.claim.create` asks the chart to provision its own volume and defaults the
store to the local filesystem. Helm cannot delete that volume, so uninstalling
never destroys the data and reinstalling recovers it.

This is for evaluation. It gives up the property the product is built on, where
the bucket holds the data and any node can be replaced without losing it. For
anything you intend to keep, use a bucket.

If you have the Polign CLI, `polign-apikey generate` produces a key the same way.

## Install with a bucket

Prepare a bucket/prefix and a workload identity that can read, write, list and
delete its objects. Use a dedicated prefix for this deployment. For AWS, grant
`s3:ListBucket` on the bucket and `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`
on the prefix, plus any permissions required by your bucket's encryption policy.
Do not point a new deployment at another running writer's prefix.

For EKS with an existing IAM role for service accounts, save `polign-values.yaml`:

```yaml
store:
  uri: s3://your-bucket/polign
serviceAccount:
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/polign
env:
  - name: AWS_REGION
    value: us-east-1
```

The role's trust policy must authorize the chart's namespace and service account
(`polign/polign` in this example). EKS Pod Identity can instead associate that
service account with a role. GKE Workload Identity and Azure Workload Identity
use the same `serviceAccount.annotations`, `podLabels`, and `env` values with
`gcs://bucket/prefix` or `az://account/container/prefix`. Cloud identity setup
remains the cluster administrator's responsibility; the chart creates no cloud
resources or Kubernetes RoleBindings.

Create the API key before installing. It never has to leave your secret
manager, and making one needs neither a cluster nor a bucket:

```sh
kubectl create namespace polign
kubectl -n polign create secret generic polign-key \
  --from-literal=api-key="plgn_$(openssl rand -hex 8)_$(openssl rand -hex 32)"
```

Then install, naming that Secret. The server registers the key as it starts:

```sh
helm install polign oci://ghcr.io/polign/charts/polign \
  --version 0.2.0 --namespace polign -f polign-values.yaml \
  --set auth.existingSecret=polign-key --wait --timeout 10m
```

Add `--set auth.namespace=recall` to bind the key to one namespace.

That is the whole install. Adoption is idempotent, so restarts and upgrades
re-check the same key rather than minting another, and a restart can never
rebind an existing key to a different secret or namespace. The chart never
creates the Secret, so the key stays in your secret manager and out of Helm
values and source control.

Leaving `auth.existingSecret` empty keeps the older behaviour, where no key
exists until you create one against the running pod.

For S3-compatible stores, provide `AWS_ENDPOINT_URL_S3` and
`AWS_S3_FORCE_PATH_STYLE=true`. Static credentials, when needed, belong in an
existing Secret referenced through `envFrom` or `env[].valueFrom.secretKeyRef`.
Do not put credentials directly in Helm values or source control.

## Connect Recall

The Service is cluster-internal. API-key authentication is always enabled.
An application running in the cluster connects to `http://polign.polign.svc:23000`
and reads the key from the same Secret the server adopted.

For a second key, scoped differently from the one the deployment supplied, mint
it against the pod's existing bucket identity:

```sh
kubectl -n polign exec deployment/polign -- \
  polign-apikey -store s3://your-bucket/polign create -namespace recall-demo
```

Only its hash is stored, so save what that prints. For a local Claude Code or
Python session:

```sh
kubectl -n polign port-forward service/polign 23100:23000
```

In the terminal that launches the client, set `POLIGN_URL` to
`http://127.0.0.1:23100`, `POLIGN_API_KEY` to your key, and `POLIGN_COLLECTION`
to a dedicated memory collection. The Recall plugin runs its MCP process on
the client machine; Kubernetes runs the database. Any explicit plugin MCP
configuration overrides inherited environment settings, so update that too
if you previously configured a local demo endpoint.

For traffic crossing a network, enable TLS with `tls.existingSecret` naming a
`kubernetes.io/tls` Secret, or use your cluster's authenticated encrypted proxy.
The Secret enables TLS on HTTP and gRPC, and changes all probes to HTTPS.
Clients must trust the CA and use a hostname covered by the certificate; use
that hostname when accessing a TLS server through a port-forward as well.
The unauthenticated administrative listener and key-management API stay off.

## Storage, upgrades, and recovery

Durable records, log and indexes live in the bucket. The local cache is a bounded
`emptyDir` and can be discarded. Cache defaults are 1 GiB of entries within a
2 GiB volume; increase `cache.bytes`, `cache.sizeLimit`, and ephemeral-storage
resources together. Memory/CPU values are starting budgets, not capacity claims.

This chart accepts exactly one replica. It uses `Recreate`, so upgrades cause
brief downtime while the previous server drains and its replacement replays the
log. Installing a second Helm release against the same prefix bypasses this
chart-level restriction and is outside this deployment's supported topology.

`/healthz` checks the HTTP process. `/readyz` becomes available after startup
restore and retained-log catch-up; it returns 503 during the five-second shutdown
delay while health remains 200. The startup probe budget is configurable and
defaults to 30 minutes, as described below. Request
draining shares a ten-second deadline across HTTP and gRPC, followed by up to
fifteen seconds for embedded persistence. The pod gets 45 seconds to terminate.
Readiness does not actively probe cloud storage on every request.

Startup restore and write-log catch-up must finish inside
`startupProbe.periodSeconds * startupProbe.failureThreshold`, 30 minutes by
default. A store that takes longer never becomes ready: the pod is killed and
restarts into the same replay indefinitely. Measure your restore time and set
the threshold above it before a large store is deployed.

Pin an image version or `image.digest`. To upgrade:

```sh
helm upgrade polign oci://ghcr.io/polign/charts/polign \
  --version 0.2.0 --namespace polign -f polign-values.yaml --wait --timeout 10m
```

Uninstalling the chart deletes its Deployment, Service, and ServiceAccount. It
does not delete bucket contents or an externally managed PVC. Reinstall with
the same store configuration to recover. A Helm rollback changes manifests;
it does not roll back database writes or promise storage-format compatibility
with an older binary.

## Filesystem storage

`store.claim.create=true` provisions the volume, which is what the quickstart
above uses. Size it with `store.claim.size` and pick a class with
`store.claim.storageClass`. To manage the volume yourself instead, create the
claim first and name it in `store.existingClaim`; set one or the other, never
both. Either way the volume must allow writes by UID and GID 65532, which is
the fsGroup the pod requests.

Durability here is the volume's, not the bucket's. A kind volume survives pod
replacement but disappears with the cluster.

## Where the chart comes from

The chart and its image live in a public registry rather than one cloud's
catalog, so the same command installs on EKS, GKE, AKS, on-premises, and any
other conformant cluster. Nothing in the chart is cloud-specific; the store URI
and the service account annotations are what change. The packaged chart is also
attached to the
[chart release](https://github.com/Polign/polign/releases/tag/polign-chart-v0.2.0)
as `polign-0.2.0.tgz`.

Every published chart is signed with Sigstore, with no key for anyone to hold or
leak. Verifying is optional but cheap, and the digest is stronger than the tag
because a tag can be moved later:

```sh
cosign verify ghcr.io/polign/charts/polign:0.2.0 \
  --certificate-identity-regexp '^https://github\.com/Polign/polign/\.github/workflows/chart-release\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

A signature proves this repository's release workflow published the chart. It
says nothing about whether the configuration you supply is safe.

## Validation

`tests/kubernetes/chart.py` rejects unsafe replica and filesystem configurations.
`tests/kubernetes/smoke.py` creates its own namespace in an explicitly selected
kind cluster and exercises both S3-compatible storage and a PVC: authenticated
memory writes, correction/history, readiness during termination, pod replacement,
a Helm upgrade, and durable retraction across uninstall/reinstall. The upgrade
test changes the pod template using the same candidate binary; it does not
claim compatibility with every historical server version. GCS, Azure and real
cloud workload identity require validation in the target cloud environment.

The server CI runs the suite against its current source. Container publication
runs it again against the verified released binaries before pushing an image.
