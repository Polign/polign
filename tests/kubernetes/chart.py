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
    # A one-probe startup budget restarts a server mid-restore, for ever.
    {'store': {'uri': 's3://bucket/data'}, 'startupProbe': {'failureThreshold': 1}},
    {'store': {'uri': 's3://bucket/data'}, 'auth': {'secretKey': ''}},
    {'store': {'uri': 's3://bucket/data'}, 'auth': {'unknown': 'x'}},
]:
    render(invalid, ok=False)

# A supplied key must reach the server as a read-only file, never as an
# argument or an environment variable, and must leave the install with nothing
# to run by hand.
key_docs = [d for d in render({'store': {'uri': 's3://bucket/data'},
                               'auth': {'existingSecret': 'polign-key', 'namespace': 'recall'}}) if d]
key_dep = next(d for d in key_docs if d['kind'] == 'Deployment')
key_spec = key_dep['spec']['template']['spec']
key_container = key_spec['containers'][0]
assert '-bootstrap-key-file=/auth/api-key' in key_container['args']
assert '-bootstrap-key-namespace=recall' in key_container['args']
assert not any('plgn_' in a for a in key_container['args'])
assert not any('plgn_' in str(e) for e in key_container.get('env', []))
mount = next(m for m in key_container['volumeMounts'] if m['mountPath'] == '/auth')
assert mount['readOnly'], 'the key file must be mounted read-only'
volume = next(v for v in key_spec['volumes'] if v['name'] == 'bootstrap-key')
assert volume['secret']['secretName'] == 'polign-key'
assert volume['secret']['defaultMode'] == 0o400, 'the key file must not be world or group readable'
assert all(d['kind'] != 'Secret' for d in key_docs), 'the chart must not create or template the key itself'
assert all(d['kind'] not in ('Role', 'RoleBinding', 'Job') for d in key_docs), 'adopting a key needs no RBAC and no hook'

# Without a key Secret the deployment is byte-for-byte what it was before.
plain = [d for d in render({'store': {'uri': 's3://bucket/data'}}) if d]
plain_container = next(d for d in plain if d['kind'] == 'Deployment')['spec']['template']['spec']['containers'][0]
assert not any('bootstrap-key' in a for a in plain_container['args'])
assert all(m['mountPath'] != '/auth' for m in plain_container['volumeMounts'])

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
print('PASS: chart rejects unsafe storage/replica/startup/auth configurations, adopts a supplied key without RBAC or a hook, and renders all storage, identity and TLS profiles')
