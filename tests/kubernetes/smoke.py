"""Exercise the chart in an explicitly selected disposable kind cluster.

Requires kubectl, helm, Python and boto3. Creates and removes its own namespace.
The test image must already be available to the cluster. Never uses the default
kubectl context. Test keys stay in memory and are never printed.
"""
import argparse
import contextlib
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config

parser = argparse.ArgumentParser()
parser.add_argument('--context', required=True)
parser.add_argument('--image', required=True, help='repository:tag')
parser.add_argument('--chart', default=str(Path(__file__).resolve().parents[2] / 'charts/polign'))
args = parser.parse_args()
if not args.context.startswith('kind-'):
    parser.error('this destructive recovery test requires an isolated kind context')
namespace = 'polign-test-' + uuid.uuid4().hex[:8]
repository, tag = args.image.rsplit(':', 1)


def run(command, *, data=None, check=True):
    try:
        result = subprocess.run(command, input=data, text=True, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{command[0]} timed out') from None
    if check and result.returncode:
        raise RuntimeError(f'{command[0]} failed: {result.stderr}')
    return result.stdout


def kubectl(*params, data=None, check=True):
    return run(['kubectl', '--context', args.context, '-n', namespace, *params], data=data, check=check)


def helm(*params):
    return run(['helm', '--kube-context', args.context, '-n', namespace, *params])


def apply(obj):
    kubectl('apply', '-f', '-', data=json.dumps(obj))


@contextlib.contextmanager
def forward(resource, remote):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(['kubectl', '--context', args.context, '-n', namespace,
                             'port-forward', resource, f'{port}:{remote}'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError('port-forward exited')
                time.sleep(.1)
        else:
            raise RuntimeError('port-forward timed out')
        yield f'http://127.0.0.1:{port}'
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def status(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def ready_pod(old_uid=None):
    for _ in range(180):
        pods = json.loads(kubectl('get', 'pods', '-l', 'app.kubernetes.io/instance=polign', '-o', 'json'))
        for pod in pods['items']:
            if pod['metadata']['uid'] == old_uid or pod['metadata'].get('deletionTimestamp'):
                continue
            if any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status'].get('conditions', [])):
                return pod
        time.sleep(1)
    raise RuntimeError('replacement pod did not become ready')


def mcp(key, name, arguments):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': name, 'arguments': arguments}}
    for attempt in range(10):
        result = kubectl('exec', '-i', 'deployment/polign', '--', 'polign',
                         '-url', 'http://127.0.0.1:23000', '-key', key,
                         'mcp', '-collection', 'recall_smoke', '-memory-only', '-write',
                         data=json.dumps(request) + '\n')
        response = json.loads(result)
        if 'error' not in response and not response['result'].get('isError'):
            return json.loads(response['result']['content'][0]['text'])
        # Startup persistence can change the visible log while a view is built.
        # Retry only this explicit read error, never uncertain writes.
        if name == 'recall' and 'log changed while materializing; retry' in result and attempt < 9:
            time.sleep(.2)
            continue
        raise RuntimeError(f'MCP {name}: {response}')


def exercise(store, extra):
    helm('install', 'polign', args.chart, '--set-string', f'store.uri={store}',
         '--set-string', f'image.repository={repository}', '--set-string', f'image.tag={tag}',
         '--set', 'resources.requests.cpu=100m', '--wait', '--timeout', '180s', *extra)
    pod = ready_pod()
    with forward('pod/' + pod['metadata']['name'], 23000) as url:
        assert status(url + '/healthz') == 200
        assert status(url + '/readyz') == 200
        assert status(url + '/v1/collections/recall_smoke/vectors') == 401
    output = kubectl('exec', 'deployment/polign', '--', 'polign-apikey', '-store', store,
                     'create', '-namespace', 'test:agent')
    key = next(line.strip() for line in output.splitlines() if line.startswith('plgn_'))
    pair = {'subject': 'kubernetes-test', 'predicate': 'prefers_editor'}
    mcp(key, 'remember', {**pair, 'value': 'vim'})
    mcp(key, 'remember', {**pair, 'value': 'neovim'})

    def check_current():
        assert [b['value'] for b in mcp(key, 'recall', pair)] == ['neovim']
        assert len(mcp(key, 'memory_history', pair)) == 2

    check_current()
    with forward('pod/' + pod['metadata']['name'], 23000) as url:
        kubectl('delete', 'pod', pod['metadata']['name'], '--wait=false')
        for _ in range(30):
            if status(url + '/readyz') == 503:
                assert status(url + '/healthz') == 200
                break
            time.sleep(.1)
        else:
            raise AssertionError('terminating pod never became unready while live')
    pod = ready_pod(pod['metadata']['uid'])
    check_current()
    # A Helm revision that changes the pod template must replace the pod and
    # keep the same bucket/PVC. This tests orchestration, not format migration.
    helm('upgrade', 'polign', args.chart, '--reuse-values', '--set-string',
         'podAnnotations.rollout=upgrade-test', '--wait', '--timeout', '180s')
    pod = ready_pod(pod['metadata']['uid'])
    check_current()
    mcp(key, 'forget', {**pair, 'value': 'neovim'})
    helm('uninstall', 'polign', '--wait', '--timeout', '180s')
    helm('install', 'polign', args.chart, '--set-string', f'store.uri={store}',
         '--set-string', f'image.repository={repository}', '--set-string', f'image.tag={tag}',
         '--set', 'resources.requests.cpu=100m', '--wait', '--timeout', '180s', *extra)
    ready_pod()
    assert mcp(key, 'recall', pair) == []
    assert len(mcp(key, 'memory_history', pair)) == 3
    helm('uninstall', 'polign', '--wait', '--timeout', '180s')
    print(f'PASS {store.split(":")[0]}: authentication, correction, draining, pod replacement, Helm upgrade, uninstall/reinstall, history and retraction', flush=True)


kubectl('create', 'namespace', namespace)
try:
    # S3 lives outside the Polign pod, so its cache and entire pod can disappear.
    # Use Moto's official image: the former MinIO image is no longer public.
    secret = uuid.uuid4().hex
    apply({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 's3', 'labels': {'app': 's3'}},
           'spec': {'containers': [{'name': 's3',
               'image': 'ghcr.io/getmoto/motoserver:5.2.2@sha256:d8ae5edc2bf080e7e4c13f9bd4b29b53ac3b4427e92956318db3dbe23ec43eb7',
               'ports': [{'containerPort': 5000}],
               'readinessProbe': {'httpGet': {'path': '/moto-api/', 'port': 5000}, 'periodSeconds': 2}}]}})
    apply({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 's3'},
           'spec': {'selector': {'app': 's3'}, 'ports': [{'port': 5000, 'targetPort': 5000}]}})
    kubectl('wait', '--for=condition=Ready', 'pod/s3', '--timeout=180s')
    with forward('pod/s3', 5000) as url:
        s3 = boto3.client('s3', endpoint_url=url, aws_access_key_id='polign-test',
                          aws_secret_access_key=secret, region_name='us-east-1',
                          config=Config(s3={'addressing_style': 'path'}))
        s3.create_bucket(Bucket='polign-test')
    apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 's3-test'}, 'stringData': {
        'AWS_ACCESS_KEY_ID': 'polign-test', 'AWS_SECRET_ACCESS_KEY': secret,
        'AWS_REGION': 'us-east-1', 'AWS_ENDPOINT_URL_S3': 'http://s3:5000', 'AWS_S3_FORCE_PATH_STYLE': 'true'}})
    exercise('s3://polign-test/data', ['--set', 'envFrom[0].secretRef.name=s3-test'])

    apply({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'polign-data'},
           'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '1Gi'}}}})
    exercise('fs:/var/lib/polign', ['--set', 'store.existingClaim=polign-data'])
    kubectl('get', 'pvc', 'polign-data')
except BaseException:
    print(kubectl('get', 'pods', '-o', 'wide', check=False))
    print(kubectl('logs', 'deployment/polign', '--tail=60', check=False))
    print(kubectl('get', 'events', '--sort-by=.lastTimestamp', check=False))
    raise
finally:
    kubectl('delete', 'namespace', namespace, '--wait=false', check=False)
