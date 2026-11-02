"""Opt-in NAS request monitoring with restricted SSH and existing notification routing."""
import hashlib
import json
from pathlib import Path
import re
from prod_nas_monitoring import labels, resource, validate, encode, update_prometheus_config
from prod_host_monitoring_dashboard import panel

NAME = 'prod-nas-nfs-collector'
CONFIG = 'prod-nas-nfs-config'
JOB = 'qnap-nas-nfs'


def rules():
    shared = {'severity': 'warning', 'scope': 'nas-nfs', 'source': 'prod-outside-in'}
    return {'groups': [{'name': 'nas-nfs', 'interval': '60s', 'rules': [
        {'alert': 'NASNFSv4Activity', 'expr': 'increase(nas_nfs_v4_compound_total{job="qnap-nas-nfs"}[30m]) > 0',
         'for': '0m', 'labels': shared, 'annotations': {'summary': 'NAS NFSv4 traffic detected in the last 30 minutes'}},
        {'alert': 'NASNFSCollectorUnavailable', 'expr': 'up{job="qnap-nas-nfs"} == 0 or absent(up{job="qnap-nas-nfs"}) or nas_nfs_collection_success{job="qnap-nas-nfs"} == 0 or (time() - nas_nfs_last_success_timestamp_seconds{job="qnap-nas-nfs"} > 900)',
         'for': '10m', 'labels': shared, 'annotations': {'summary': 'NAS NFS statistics collection is unavailable'}}
    ]}]}


def add_monitoring(manifest, address, image):
    validate(address, 'prometheus')
    if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}', image):
        raise ValueError('NAS NFS image must be digest pinned')
    def find(kind, name):
        return next(x for x in manifest['items'] if x['kind'] == kind and x['metadata']['name'] == name)
    selector = {'matchLabels': labels(NAME)}
    prom = {'matchLabels': labels('prod-prometheus')}
    data = {'nas_nfs_collector.py': Path(__file__).with_name('nas_nfs_collector.py').read_text(), 'alerts.json': encode(rules())}
    template = {'metadata': {'labels': labels(NAME), 'annotations': {'checksum/config': hashlib.sha256(encode(data).encode()).hexdigest()}}, 'spec': {
        'automountServiceAccountToken': False, 'nodeSelector': {'kubernetes.io/os': 'linux'},
        'tolerations': [{'key': 'node-role.kubernetes.io/' + role, 'operator': 'Exists', 'effect': 'NoSchedule'} for role in ('control-plane', 'master')],
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001, 'fsGroup': 10001},
        'containers': [{'name': 'collector', 'image': image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python3', '/app/nas_nfs_collector.py', '--host', address],
            'ports': [{'name': 'http', 'containerPort': 9189}],
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '96Mi'}},
            'readinessProbe': {'httpGet': {'path': '/healthz', 'port': 'http'}},
            'livenessProbe': {'httpGet': {'path': '/healthz', 'port': 'http'}, 'initialDelaySeconds': 10},
            'volumeMounts': [{'name': 'code', 'mountPath': '/app', 'readOnly': True}, {'name': 'ssh', 'mountPath': '/etc/nas-ssh', 'readOnly': True}]}],
        'volumes': [{'name': 'code', 'configMap': {'name': CONFIG}}, {'name': 'ssh', 'secret': {'secretName': 'prod-nas-nfs-ssh', 'defaultMode': 288}}]}}
    manifest['items'].extend([
        resource('ConfigMap', CONFIG, data=data),
        resource('Service', NAME, spec={'type': 'ClusterIP', 'selector': labels(NAME), 'ports': [{'name': 'http', 'port': 9189, 'targetPort': 'http', 'protocol': 'TCP'}]}),
        resource('Deployment', NAME, spec={'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': selector, 'template': template}),
        resource('NetworkPolicy', 'prod-nas-nfs-restricted', spec={'podSelector': selector, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [{'from': [{'podSelector': prom}], 'ports': [{'port': 9189, 'protocol': 'TCP'}]}], 'egress': [{'to': [{'ipBlock': {'cidr': address + '/32'}}], 'ports': [{'port': 22, 'protocol': 'TCP'}]}]}),
        resource('NetworkPolicy', 'prod-nas-nfs-prometheus-egress', spec={'podSelector': prom, 'policyTypes': ['Egress'], 'egress': [{'to': [{'podSelector': selector}], 'ports': [{'port': 9189, 'protocol': 'TCP'}]}]})])
    cm = find('ConfigMap', 'prod-prometheus-config')
    cfg = json.loads(cm['data']['prometheus.json'])
    cfg['scrape_configs'].append({'job_name': JOB, 'scrape_interval': '30s', 'scrape_timeout': '5s', 'sample_limit': 100,
        'static_configs': [{'targets': [NAME + ':9189'], 'labels': {'instance': address}}],
        'metric_relabel_configs': [{'action': 'keep', 'source_labels': ['__name__'], 'regex': 'nas_nfs_.*'}]})
    cfg.setdefault('rule_files', []).append('/etc/prometheus/nas-nfs-rules/alerts.json')
    update_prometheus_config(manifest, cfg)
    pt = find('Deployment', 'prod-prometheus')['spec']['template']
    pt['spec']['volumes'].append({'name': 'nas-nfs-rules', 'configMap': {'name': CONFIG, 'items': [{'key': 'alerts.json', 'path': 'alerts.json'}]}})
    pt['spec']['containers'][0]['volumeMounts'].append({'name': 'nas-nfs-rules', 'mountPath': '/etc/prometheus/nas-nfs-rules', 'readOnly': True})
    ann = pt['metadata']['annotations']
    ann['checksum/config'] = hashlib.sha256((ann['checksum/config'] + data['alerts.json']).encode()).hexdigest()
    gd = find('ConfigMap', 'prod-grafana-provisioning')['data']
    dashboard = json.loads(gd['qnap-nas.json'])
    y = max(p['gridPos']['y'] + p['gridPos']['h'] for p in dashboard['panels'])
    n = max(p['id'] for p in dashboard['panels'])
    for i, (title, expr, unit) in enumerate([
        ('NFSv4 cumulative requests', 'nas_nfs_v4_compound_total{job="qnap-nas-nfs"}', 'short'),
        ('NFSv4 requests (30m)', 'increase(nas_nfs_v4_compound_total{job="qnap-nas-nfs"}[30m])', 'short'),
        ('NFS collection success', 'nas_nfs_collection_success{job="qnap-nas-nfs"}', 'short'),
        ('NFS collection age', 'time()-nas_nfs_last_success_timestamp_seconds{job="qnap-nas-nfs"}', 's'),
        ('Firing NFS alerts', 'sum(ALERTS{scope="nas-nfs",alertstate="firing"}) or vector(0)', 'short')]):
        p = panel(n+i+1, title, expr, unit)
        p['gridPos'].update(x=i % 2 * 12, y=y+i//2*8)
        dashboard['panels'].append(p)
    gd['qnap-nas.json'] = encode(dashboard)
    find('Deployment', 'prod-grafana')['spec']['template']['metadata']['annotations']['checksum/provisioning'] = hashlib.sha256(encode(gd).encode()).hexdigest()
