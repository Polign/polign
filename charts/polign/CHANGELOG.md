# Polign Helm chart 0.2.0

Requires Polign 0.6.6 or newer, for the server flag that adopts a supplied key.

- `store.claim.create` provisions the volume, so Polign can be evaluated with no
  bucket, no cloud identity and nothing installed locally. It defaults the store
  to the local filesystem, and the claim carries Helm's keep policy, so an
  uninstall cannot destroy the data and a reinstall recovers it. This trades
  away the durability model the product is built on, where the bucket holds the
  data and any node can be replaced, so the post-install notes say so whenever
  it is used. Managing the volume yourself through `store.existingClaim` still
  works; set one or the other, never both.
- Shorter documented install. A key is now made with any source of randomness
  rather than by installing the Polign CLI first, the simple path uses install
  flags instead of a values file, and signature verification moved out of the
  install steps into its own section, since it is optional.
- Supply the API key from a Secret through `auth.existingSecret` and the server
  registers it at startup. Installing is now one declarative step instead of a
  Helm command followed by an operator running a mint command against the pod
  and copying the result somewhere, which no deployment tool could express.
  Generate the key with `polign-apikey generate`, which needs neither a cluster
  nor a bucket. The chart never creates the Secret, so the key stays in whatever
  already manages secrets. Adoption is idempotent across restarts and upgrades,
  and refuses to rebind a key that already exists to a different secret or
  namespace. The key reaches the server as a read-only file, never as an
  argument or an environment variable, and needs no RBAC, no Job, and no hook.
  Leaving `auth.existingSecret` empty keeps the previous behaviour.
- `startupProbe.periodSeconds` and `startupProbe.failureThreshold` are now
  configurable, and the budget default rises from 10 to 30 minutes. A store
  whose restore ran longer than the old fixed budget never became ready: the pod
  was killed and restarted into the same replay indefinitely, which hit the
  deployments holding the most data hardest. Measure your restore time and set
  the threshold above it.
- Publish Artifact Hub metadata, so the chart is discoverable from a
  vendor-neutral index rather than only from a direct registry reference or one
  cloud vendor's catalog. Adds category, license, maintainer, icon, image and
  link annotations.
- Sign the published chart with Sigstore keyless signing, and verify the
  signature in the release job against this repository's workflow identity.
  Signing and verification use the digest rather than the tag.
- Claim repository ownership through the reserved `artifacthub.io` tag in the
  chart's OCI repository.

Try it: `helm install polign oci://ghcr.io/polign/charts/polign --version 0.2.0 --namespace polign --set store.claim.create=true --set auth.existingSecret=polign-key`

Install: `helm install polign oci://ghcr.io/polign/charts/polign --version 0.2.0 --set store.uri=s3://your-bucket/polign --set auth.existingSecret=polign-key`

See the chart README for workload identity, key creation, TLS, signature
verification, and Recall setup.
