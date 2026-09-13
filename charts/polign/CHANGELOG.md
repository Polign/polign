# Polign Helm chart 0.1.0

- Deploy Polign 0.6.5 on Kubernetes with one server, a ClusterIP Service, API-key
  authentication, workload-identity configuration, and optional TLS.
- Keep durable state in a supplied bucket or existing PVC, and mount a disposable
  bounded cache. Refuse multiple replicas and filesystem storage without a PVC.
- Use startup/readiness/liveness probes, non-root execution, a read-only root
  filesystem, and a bounded shutdown period. Upgrades use Recreate with downtime.
- Test corrections, history, pod replacement, Helm upgrades and reinstall
  recovery against S3-compatible storage and a PVC.

Install: `helm install polign oci://ghcr.io/polign/charts/polign --version 0.1.0 --set store.uri=s3://your-bucket/polign`

See the chart README for workload identity, key creation, TLS, and Recall setup.
