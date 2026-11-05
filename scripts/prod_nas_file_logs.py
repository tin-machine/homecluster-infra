"""Opt-in production NAS file collection with private credentials and state."""
import hashlib
import json
from pathlib import Path
import re

from prod_nas_monitoring import encode, labels, resource, update_prometheus_config, validate
from prod_host_monitoring_dashboard import panel

NAME = 'prod-nas-file-logs'
CONFIG = NAME + '-config'
STATE = NAME + '-state'
JOB = 'qnap-nas-file-logs'


def rules():
    return {'groups': [{'name': 'nas-file-logs', 'interval': '60s', 'rules': [
        {'alert': 'NASFileLogsUnavailable', 'expr': 'up{job="qnap-nas-file-logs"} == 0 or absent(up{job="qnap-nas-file-logs"}) or nas_file_logs_collection_success{job="qnap-nas-file-logs"} == 0 or nas_file_logs_state_valid{job="qnap-nas-file-logs"} == 0 or (time()-nas_file_logs_last_success_timestamp_seconds{job="qnap-nas-file-logs"} > 900)',
         'for': '10m', 'labels': {'severity': 'warning', 'scope': 'nas-file-logs', 'source': 'prod-outside-in'},
         'annotations': {'summary': 'NAS file log collection or delivery is unavailable'}},
        {'alert': 'NASFileLogsRotationGap', 'expr': 'increase(nas_file_logs_rotation_gaps_total{job="qnap-nas-file-logs"}[1h]) > 0',
         'for': '0m', 'labels': {'severity': 'warning', 'scope': 'nas-file-logs', 'source': 'prod-outside-in'},
         'annotations': {'summary': 'NAS log rotation exceeded the retained cursor history'}},
    ]}]}


def add_monitoring(manifest, address, node, image, filter_normal=False):
    validate(address, 'prometheus')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', node or ''):
        raise ValueError('file logs require an explicit production node')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}', image or ''):
        raise ValueError('file log image must be digest pinned')
    def find(kind, name):
        return next(x for x in manifest['items'] if x['kind'] == kind and x['metadata']['name'] == name)
    selector = {'matchLabels': labels(NAME)}
    prom = {'matchLabels': labels('prod-prometheus')}
    loki = {'matchLabels': labels('prod-loki')}
    data = {name: Path(__file__).with_name(name).read_text() for name in
            ('nas_file_logs_collector.py', 'nas_file_logs_core.py', 'nas_log_normal_patterns.py')}
    data['alerts.json'] = encode(rules())
    pod = {
        'automountServiceAccountToken': False,
        'nodeSelector': {'kubernetes.io/os': 'linux', 'kubernetes.io/hostname': node},
        'tolerations': [{'key': 'node-role.kubernetes.io/' + role, 'operator': 'Exists', 'effect': 'NoSchedule'} for role in ('control-plane', 'master')],
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001, 'fsGroup': 10001},
        'containers': [{'name': 'collector', 'image': image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python3', '/app/nas_file_logs_collector.py', '--host', address,
                        '--filter-normal', str(filter_normal).lower()],
            'env': [{'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}],
            'ports': [{'name': 'http', 'containerPort': 9188}],
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'resources': {'requests': {'cpu': '10m', 'memory': '48Mi'}, 'limits': {'cpu': '200m', 'memory': '256Mi'}},
            'readinessProbe': {'httpGet': {'path': '/healthz', 'port': 'http'}},
            'livenessProbe': {'httpGet': {'path': '/healthz', 'port': 'http'}, 'initialDelaySeconds': 10},
            'volumeMounts': [{'name': 'code', 'mountPath': '/app', 'readOnly': True},
                             {'name': 'ssh', 'mountPath': '/etc/nas-ssh', 'readOnly': True},
                             {'name': 'state', 'mountPath': '/data'}]}],
        'volumes': [{'name': 'code', 'configMap': {'name': CONFIG}},
                    {'name': 'ssh', 'secret': {'secretName': NAME + '-ssh', 'defaultMode': 288}},
                    {'name': 'state', 'persistentVolumeClaim': {'claimName': STATE}}],
    }
    manifest['items'].extend([
        resource('ConfigMap', CONFIG, data=data),
        resource('PersistentVolumeClaim', STATE, spec={'accessModes': ['ReadWriteOnce'], 'storageClassName': 'local-path', 'resources': {'requests': {'storage': '128Mi'}}}),
        resource('Service', NAME, spec={'type': 'ClusterIP', 'selector': labels(NAME), 'ports': [{'name': 'http', 'port': 9188, 'targetPort': 'http', 'protocol': 'TCP'}]}),
        resource('Deployment', NAME, spec={'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': selector,
            'template': {'metadata': {'labels': labels(NAME), 'annotations': {'checksum/config': hashlib.sha256(encode(data).encode()).hexdigest()}}, 'spec': pod}}),
        resource('NetworkPolicy', NAME + '-restricted', spec={'podSelector': selector, 'policyTypes': ['Ingress', 'Egress'],
            'ingress': [{'from': [{'podSelector': prom}], 'ports': [{'port': 9188, 'protocol': 'TCP'}]}],
            'egress': [{'to': [{'ipBlock': {'cidr': address + '/32'}}], 'ports': [{'port': 22, 'protocol': 'TCP'}]},
                       {'to': [{'podSelector': loki}], 'ports': [{'port': 3100, 'protocol': 'TCP'}]},
                       {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}, 'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                        'ports': [{'port': 53, 'protocol': 'UDP'}, {'port': 53, 'protocol': 'TCP'}]}]}),
        resource('NetworkPolicy', NAME + '-prometheus-egress', spec={'podSelector': prom, 'policyTypes': ['Egress'],
            'egress': [{'to': [{'podSelector': selector}], 'ports': [{'port': 9188, 'protocol': 'TCP'}]}]}),
        resource('NetworkPolicy', NAME + '-loki-ingress', spec={'podSelector': loki, 'policyTypes': ['Ingress'],
            'ingress': [{'from': [{'podSelector': selector}], 'ports': [{'port': 3100, 'protocol': 'TCP'}]}]}),
    ])
    cfg = json.loads(find('ConfigMap', 'prod-prometheus-config')['data']['prometheus.json'])
    cfg['scrape_configs'].append({'job_name': JOB, 'scrape_interval': '30s', 'scrape_timeout': '5s', 'sample_limit': 500,
        'static_configs': [{'targets': [NAME + ':9188']}],
        'metric_relabel_configs': [{'action': 'keep', 'source_labels': ['__name__'], 'regex': 'nas_file_logs_.*'}]})
    cfg.setdefault('rule_files', []).append('/etc/prometheus/nas-file-log-rules/alerts.json')
    update_prometheus_config(manifest, cfg)
    pt = find('Deployment', 'prod-prometheus')['spec']['template']
    pt['spec']['volumes'].append({'name': 'nas-file-log-rules', 'configMap': {'name': CONFIG, 'items': [{'key': 'alerts.json', 'path': 'alerts.json'}]}})
    pt['spec']['containers'][0]['volumeMounts'].append({'name': 'nas-file-log-rules', 'mountPath': '/etc/prometheus/nas-file-log-rules', 'readOnly': True})
    ann = pt['metadata']['annotations']
    ann['checksum/config'] = hashlib.sha256((ann['checksum/config'] + data['alerts.json']).encode()).hexdigest()
    gd = find('ConfigMap', 'prod-grafana-provisioning')['data']
    dashboard = json.loads(gd['qnap-nas.json'])
    y = max(p['gridPos']['y'] + p['gridPos']['h'] for p in dashboard['panels'])
    n = max(p['id'] for p in dashboard['panels'])
    for i, (title, expr) in enumerate([
        ('NAS file collection success', 'nas_file_logs_collection_success{job="qnap-nas-file-logs"}'),
        ('NAS normal logs excluded (1h)', '(sum(increase(nas_file_logs_filtered_total{job="qnap-nas-file-logs"}[1h])) or vector(0)) + (sum(increase(loki_process_dropped_lines_total{job="qnap-nas-collector",reason=~"nas_normal_.*"}[1h])) or vector(0))'),
    ]):
        item = panel(n + 1 + i, title, expr, 'short', kind='stat')
        item['gridPos'].update(x=12 * i, y=y)
        dashboard['panels'].append(item)
    dashboard['panels'].append({'id': n + 3, 'title': 'NAS file logs — non-normal / unclassified' if filter_normal else 'NAS file logs', 'type': 'logs',
        'description': 'Time column is observation time. JSON source_time is the original NAS time when available; bootstrap=true marks retained history at first collection.',
        'datasource': {'type': 'loki', 'uid': 'prod-loki'},
        'targets': [{'refId': 'A', 'expr': '{environment="production",source="qnap-filelog"} | json | line_format "{{.log_kind}} {{.source_time}} bootstrap={{.bootstrap}} {{.message}}"'}],
        'gridPos': {'x': 0, 'y': y + 8, 'w': 24, 'h': 14},
        'options': {'showTime': True, 'showLabels': False, 'enableLogDetails': True, 'wrapLogMessage': True, 'sortOrder': 'Descending'}})
    gd['qnap-nas.json'] = encode(dashboard)
    find('Deployment', 'prod-grafana')['spec']['template']['metadata']['annotations']['checksum/provisioning'] = hashlib.sha256(encode(gd).encode()).hexdigest()
