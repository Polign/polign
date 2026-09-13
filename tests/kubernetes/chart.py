"""Check configurations that could lose data or expose the deployment."""
import json
from pathlib import Path
import subprocess
import yaml

chart = str(Path(__file__).resolve().parents[2] / 'charts/polign')


def render(values, ok=True):
    result = subprocess.run(['helm', 'template', 'polign', chart, '-f', '-'],
                            input=json.dumps(values), text=True, capture_output=True)
    assert (result.returncode == 0) == ok, result.stderr
    return list(yaml.safe_load_all(result.stdout)) if ok else []


for invalid in [
    {},
    {'store': {'uri': 'fs:/var/lib/polign'}},
    {'store': {'uri': 'fs:/somewhere-else', 'existingClaim': 'data'}},
    {'store': {'uri': 's3://bucket/data'}, 'replicaCount': 2},
    {'store': {'uri': 's3://bucket/data', 'existingClaim': 'ignored'}},
    {'store': {'uri': 's3://bucket/data'}, 'serviceAccount': {'create': False}},
    {'store': {'uri': 's3://bucket/data'}, 'podLabels': {'app.kubernetes.io/name': 'other'}},
]:
    render(invalid, ok=False)

for uri, extra in [
    ('s3://bucket/data', {}), ('gcs://bucket/data', {}), ('az://account/container/data', {}),
    ('fs:/var/lib/polign', {'store': {'existingClaim': 'data'}}),
    ('s3://bucket/data', {'tls': {'existingSecret': 'tls'}, 'serviceAccount': {'create': False, 'name': 'workload'}}),
]:
    values = {'store': {'uri': uri, **extra.pop('store', {})}, **extra}
    docs = [d for d in render(values) if d]
    dep = next(d for d in docs if d['kind'] == 'Deployment')
    service = next(d for d in docs if d['kind'] == 'Service')
    spec = dep['spec']['template']['spec']
    container = spec['containers'][0]
    assert dep['spec']['replicas'] == 1
    assert dep['spec']['strategy']['type'] == 'Recreate'
    assert service['spec']['type'] == 'ClusterIP'
    assert '-require-data-key' in container['args']
    assert not any(a.startswith('-admin=') or a.startswith('-management') for a in container['args'])
    assert container['securityContext']['readOnlyRootFilesystem']
    assert spec['securityContext']['runAsNonRoot']
    assert spec['terminationGracePeriodSeconds'] >= 35
    assert container['startupProbe']['httpGet']['path'] == '/readyz'
    assert container['readinessProbe']['httpGet']['path'] == '/readyz'
    assert container['livenessProbe']['httpGet']['path'] == '/healthz'
    assert all(d['kind'] != 'PersistentVolumeClaim' for d in docs), 'chart must not own/delete durable volumes'
    if uri.startswith('fs:'):
        assert next(v for v in spec['volumes'] if v['name'] == 'data')['persistentVolumeClaim']['claimName'] == 'data'
    if extra.get('tls'):
        assert container['readinessProbe']['httpGet']['scheme'] == 'HTTPS'
        assert spec['serviceAccountName'] == 'workload'
print('PASS: chart rejects unsafe storage/replica configurations and renders all storage, identity and TLS profiles')
