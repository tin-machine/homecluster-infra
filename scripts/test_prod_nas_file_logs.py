import json
from pathlib import Path
import unittest

from prod_grafana_terraform_input import terraform_manifest
from prod_nas_syslog import alloy_config
from test_prod_nas_monitoring import index
from test_prod_nas_syslog import site

IMAGE = 'registry.example/collector@sha256:' + 'a' * 64


class IntegrationTests(unittest.TestCase):
    def render(self, enabled=False, normal=False):
        return index(terraform_manifest(prometheus_site=site(), nas_address='192.0.2.10',
            include_nas_syslog=True, nas_nfs_image=IMAGE,
            nas_file_logs_image=IMAGE if enabled else '', nas_normal_filter_enabled=normal))

    def test_additive_render_preserves_existing_targets_and_panels(self):
        old, new = self.render(), self.render(True)
        self.assertEqual(len(set(new) - set(old)), 7)
        self.assertEqual({key for key in old if old[key] != new[key]},
                         {'ConfigMap/prod-prometheus-config', 'Deployment/prod-prometheus',
                          'ConfigMap/prod-grafana-provisioning', 'Deployment/prod-grafana'})
        before = json.loads(old['ConfigMap/prod-prometheus-config']['data']['prometheus.json'])
        after = json.loads(new['ConfigMap/prod-prometheus-config']['data']['prometheus.json'])
        self.assertEqual(before['scrape_configs'], after['scrape_configs'][:-1])
        dashboard = lambda x: json.loads(x['ConfigMap/prod-grafana-provisioning']['data']['qnap-nas.json'])['panels']
        self.assertEqual(dashboard(old), dashboard(new)[:-3])
        self.assertEqual(len(dashboard(new)), len({p['id'] for p in dashboard(new)}))
        self.assertGreaterEqual(dashboard(new)[-3]['gridPos']['y'], max(p['gridPos']['y'] + p['gridPos']['h'] for p in dashboard(old)))

    def test_network_and_persistent_state_are_scoped(self):
        data = self.render(True, True)
        self.assertFalse(any(key.startswith('Secret/') for key in data))
        pod = data['Deployment/prod-nas-file-logs']['spec']['template']['spec']
        self.assertEqual(pod['securityContext']['runAsUser'], 10001)
        self.assertEqual(pod['nodeSelector']['kubernetes.io/hostname'], site()['prod_metrics_target']['name'])
        self.assertEqual(pod['volumes'][1]['secret']['secretName'], 'prod-nas-file-logs-ssh')
        self.assertEqual(pod['volumes'][2]['persistentVolumeClaim']['claimName'], 'prod-nas-file-logs-state')
        self.assertTrue(pod['containers'][0]['securityContext']['readOnlyRootFilesystem'])
        policy = data['NetworkPolicy/prod-nas-file-logs-restricted']['spec']
        self.assertEqual(policy['egress'][0], {'to': [{'ipBlock': {'cidr': '192.0.2.10/32'}}], 'ports': [{'port': 22, 'protocol': 'TCP'}]})
        self.assertEqual({p['port'] for rule in policy['egress'] for p in rule['ports']}, {22, 53, 3100})
        self.assertEqual(policy['ingress'][0]['ports'], [{'port': 9188, 'protocol': 'TCP'}])
        code = data['ConfigMap/prod-nas-file-logs-config']['data']
        self.assertEqual(set(code), {'nas_file_logs_collector.py', 'nas_file_logs_core.py', 'nas_log_normal_patterns.py', 'alerts.json'})

    def test_filter_is_explicit_and_unknowns_are_not_dropped(self):
        self.assertNotIn('nas_normal_', alloy_config())
        text = alloy_config(True, '192.0.2.1')
        self.assertIn('selector = "{level=\\"info\\"}"', text)
        self.assertIn('nas_normal_smart_success', text)
        self.assertIn('nas_normal_collector_login', text)
        self.assertNotIn('nas_normal_collector_login', alloy_config(True))
        self.assertNotIn('Login Fail', text)
        self.assertIn('nas_invalid_header', text)

    def test_invalid_opt_ins(self):
        for image in ['latest', 'registry.example/collector:latest']:
            with self.assertRaises(ValueError):
                terraform_manifest(prometheus_site=site(), nas_address='192.0.2.10', nas_file_logs_image=image)
        with self.assertRaises(ValueError):
            terraform_manifest(nas_file_logs_image=IMAGE)
        with self.assertRaises(ValueError):
            terraform_manifest(nas_normal_filter_enabled=True)
        with self.assertRaises(ValueError):
            terraform_manifest(nas_normal_filter_enabled='true')


if __name__ == '__main__':
    unittest.main()
