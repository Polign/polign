# Polign on Kubernetes

Run Polign against your own bucket. The chart deploys one server with embedded
persistence and maintenance; Recall and other clients connect to its HTTP API.
Requires Polign 0.6.5+, Helm 3.22+, and Kubernetes 1.30+.
The recovery suite runs on Kubernetes 1.35.8.

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

```sh
helm install polign oci://ghcr.io/polign/charts/polign \
  --version 0.1.0 --namespace polign --create-namespace \
  -f polign-values.yaml --wait --timeout 10m
```

The same chart is attached to the
[chart release](https://github.com/Polign/polign/releases/tag/polign-chart-v0.1.0)
as `polign-0.1.0.tgz`, and can be installed from that downloaded file.

For S3-compatible stores, provide `AWS_ENDPOINT_URL_S3` and
`AWS_S3_FORCE_PATH_STYLE=true`. Static credentials, when needed, belong in an
existing Secret referenced through `envFrom` or `env[].valueFrom.secretKeyRef`.
Do not put credentials directly in Helm values or source control.

## Connect Recall

The Service is cluster-internal. API-key authentication is always enabled.
Create a namespaced key using the pod's existing bucket identity:

```sh
kubectl -n polign exec deployment/polign -- \
  polign-apikey -store s3://your-bucket/polign create -namespace recall-demo
```

Save the displayed key securely; only its hash is stored. An application running
in the cluster connects to `http://polign.polign.svc:23000` and reads its key
from a Secret. For a local Claude Code or Python session:

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
delay while health remains 200. Startup has a ten-minute probe budget. Request
draining shares a ten-second deadline across HTTP and gRPC, followed by up to
fifteen seconds for embedded persistence. The pod gets 45 seconds to terminate.
Readiness does not actively probe cloud storage on every request.

Pin an image version or `image.digest`. To upgrade:

```sh
helm upgrade polign oci://ghcr.io/polign/charts/polign \
  --version 0.1.0 --namespace polign -f polign-values.yaml --wait --timeout 10m
```

Uninstalling the chart deletes its Deployment, Service, and ServiceAccount. It
does not delete bucket contents or an externally managed PVC. Reinstall with
the same store configuration to recover. A Helm rollback changes manifests;
it does not roll back database writes or promise storage-format compatibility
with an older binary.

## Local cluster demo with a PVC

For kind or a cluster without a cloud bucket, create a PVC separately. The chart
does not own it, so Helm uninstall cannot delete it:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: polign-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 10Gi
```

Apply it in the deployment namespace, then install with
`--set store.uri=fs:/var/lib/polign --set store.existingClaim=polign-data`.
The volume must support writes by UID/GID 65532; the pod requests that fsGroup.
This mode's durability depends on the PVC and its underlying storage. A kind
volume survives pod replacement, but deleting the kind cluster removes it.

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
