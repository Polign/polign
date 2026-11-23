# Polign Helm chart 0.3.0

Installs Polign 0.17.0 by default, up from 0.6.6. Nothing else in the chart
changed: the server flags it passes are the same, and values from 0.2.0 work
unchanged. Set `image.tag` to stay on an older server.

What the server gained since 0.6.6 is in the
[Polign changelog](https://github.com/Polign/polign/releases/tag/v0.17.0).

Install: `helm install polign oci://ghcr.io/polign/charts/polign --version 0.3.0 --set store.uri=s3://your-bucket/polign --set auth.existingSecret=polign-key`

See the chart README for workload identity, key creation, TLS, signature
verification, and Recall setup.
