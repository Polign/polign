"""Check recall chart configurations that would leave the server unreachable,
unauthenticated, or quietly wrong."""
import json
from pathlib import Path
import subprocess
import yaml

chart = str(Path(__file__).resolve().parents[2] / 'charts/recall')


def render(values, ok=True):
    result = subprocess.run(['helm', 'template', 'recall', chart, '--kube-version', '1.31.0', '-f', '-'],
                            input=json.dumps(values), text=True, capture_output=True)
    assert (result.returncode == 0) == ok, result.stderr
    return list(yaml.safe_load_all(result.stdout)) if ok else []


BACKEND = {'url': 'http://polign.polign.svc:23000', 'existingSecret': 'polign-key'}

for invalid in [
    {},
    # A server nobody can call.
    {'backend': BACKEND},
    {'backend': {'url': 'http://x', 'kind': 'pgvector'}, 'auth': {'keysFile': {'existingSecret': 'k'}}},
    {'backend': {'kind': 'polign'}, 'auth': {'keysFile': {'existingSecret': 'k'}}},
    {'backend': BACKEND, 'auth': {'bootstrapKey': {'existingSecret': 'a'}}, 'serviceAccount': {'create': False}},
]:
    render(invalid, ok=False)


def by_kind(docs, kind):
    return [d for d in docs if d and d.get('kind') == kind]


# Bootstrap key only: no token mounted, no RBAC, key file mounted read-only.
docs = render({'backend': BACKEND, 'auth': {'bootstrapKey': {'existingSecret': 'recall-admin'}}})
deploy = by_kind(docs, 'Deployment')[0]
pod = deploy['spec']['template']['spec']
assert pod['automountServiceAccountToken'] is False
assert not by_kind(docs, 'ClusterRole') and not by_kind(docs, 'ClusterRoleBinding')
args = pod['containers'][0]['args']
assert '-bootstrap-key-file=/auth/key' in args and '-kubernetes' not in args
assert args[:3] == ['-backend=polign', '-url=http://polign.polign.svc:23000', 'serve']
env = {e['name']: e for e in pod['containers'][0]['env']}
assert env['POLIGN_API_KEY']['valueFrom']['secretKeyRef'] == {'name': 'polign-key', 'key': 'api-key'}
assert pod['containers'][0]['securityContext']['readOnlyRootFilesystem'] is True
assert pod['securityContext']['runAsNonRoot'] is True

# Kubernetes mode: the server's own token is mounted, TokenReview RBAC exists,
# and ServiceAccount reads appear only with a tenant label.
docs = render({'backend': BACKEND, 'kubernetes': {'enabled': True}})
pod = by_kind(docs, 'Deployment')[0]['spec']['template']['spec']
assert pod['automountServiceAccountToken'] is True
rules = by_kind(docs, 'ClusterRole')[0]['rules']
assert rules == [{'apiGroups': ['authentication.k8s.io'], 'resources': ['tokenreviews'], 'verbs': ['create']}]
args = pod['containers'][0]['args']
assert '-kubernetes' in args and '-kubernetes-audience=recall' in args and '-kubernetes-key-ttl=15m' in args
assert not any(a.startswith('-kubernetes-tenant-label') for a in args)
binding = by_kind(docs, 'ClusterRoleBinding')[0]
assert binding['subjects'][0]['name'] == 'recall' and binding['roleRef']['name'] == by_kind(docs, 'ClusterRole')[0]['metadata']['name']

docs = render({'backend': BACKEND, 'kubernetes': {'enabled': True, 'tenantLabel': 'recall.polign.com/tenant'}})
rules = by_kind(docs, 'ClusterRole')[0]['rules']
assert {'apiGroups': [''], 'resources': ['serviceaccounts'], 'verbs': ['get']} in rules
assert '-kubernetes-tenant-label=recall.polign.com/tenant' in by_kind(docs, 'Deployment')[0]['spec']['template']['spec']['containers'][0]['args']

docs = render({'backend': BACKEND, 'kubernetes': {'enabled': True, 'agentLabel': 'recall.polign.com/agent'}})
rules = by_kind(docs, 'ClusterRole')[0]['rules']
assert {'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get']} in rules
assert not any(r['resources'] == ['serviceaccounts'] for r in rules)
assert '-kubernetes-agent-label=recall.polign.com/agent' in by_kind(docs, 'Deployment')[0]['spec']['template']['spec']['containers'][0]['args']

# Qdrant: the driver's own key variable, and the resume tools can still be
# asked for (the server declines them at start when there are no leases).
docs = render({'backend': {'kind': 'qdrant', 'url': 'http://qdrant:6333', 'existingSecret': 'qk'}, 'auth': {'keysFile': {'existingSecret': 'keys'}}})
pod = by_kind(docs, 'Deployment')[0]['spec']['template']['spec']
env = {e['name']: e for e in pod['containers'][0]['env']}
assert 'QDRANT_API_KEY' in env and 'POLIGN_API_KEY' not in env
assert '-keys-file=/keys/keys' in pod['containers'][0]['args']
assert any(v['name'] == 'keys-file' and v['secret']['defaultMode'] == 0o400 for v in pod['volumes'])

# The image pins to appVersion, never latest, and a digest wins over a tag.
chart_meta = yaml.safe_load(open(Path(chart) / 'Chart.yaml'))
docs = render({'backend': BACKEND, 'kubernetes': {'enabled': True}})
image = by_kind(docs, 'Deployment')[0]['spec']['template']['spec']['containers'][0]['image']
assert image == f"ghcr.io/polign/recall:{chart_meta['appVersion']}", image
docs = render({'backend': BACKEND, 'kubernetes': {'enabled': True}, 'image': {'digest': 'sha256:' + '0' * 64}})
assert by_kind(docs, 'Deployment')[0]['spec']['template']['spec']['containers'][0]['image'].endswith('@sha256:' + '0' * 64)
print('recall chart checks passed')
