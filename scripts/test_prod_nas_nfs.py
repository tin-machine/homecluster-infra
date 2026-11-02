import json
import unittest
from prod_grafana_terraform_input import terraform_manifest
from test_prod_nas_syslog import site
from test_prod_nas_monitoring import index
from prod_nas_nfs import rules

IMAGE='registry.example/collector@sha256:'+'a'*64


class IntegrationTests(unittest.TestCase):
    def render(self, image=''):
        return index(terraform_manifest(prometheus_site=site(),nas_address='192.0.2.10',include_nas_syslog=True,nas_nfs_image=image))

    def test_additive_resources_preserve_existing_content(self):
        old=self.render();new=self.render(IMAGE)
        self.assertEqual(len(set(new)-set(old)),5)
        self.assertFalse(any(x.startswith(('Secret/','PersistentVolume')) for x in set(new)-set(old)))
        changed={k for k in old if old[k]!=new[k]}
        self.assertEqual(changed,{'ConfigMap/prod-prometheus-config','Deployment/prod-prometheus','ConfigMap/prod-grafana-provisioning','Deployment/prod-grafana'})
        before=json.loads(old['ConfigMap/prod-prometheus-config']['data']['prometheus.json']);after=json.loads(new['ConfigMap/prod-prometheus-config']['data']['prometheus.json'])
        self.assertEqual(after['scrape_configs'][:-1],before['scrape_configs']);self.assertEqual(after['rule_files'][:-1],before['rule_files'])
        dash=lambda obj:json.loads(obj['ConfigMap/prod-grafana-provisioning']['data']['qnap-nas.json'])['panels']
        self.assertEqual(dash(new)[:-5],dash(old));self.assertEqual(len({p['id'] for p in dash(new)}),len(dash(new)))
        self.assertGreaterEqual(dash(new)[-5]['gridPos']['y'],max(p['gridPos']['y']+p['gridPos']['h'] for p in dash(old)))

    def test_restricted_workload_and_network(self):
        d=self.render(IMAGE);pod=d['Deployment/prod-nas-nfs-collector']['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken']);self.assertEqual(pod['securityContext']['runAsUser'],10001)
        self.assertEqual(pod['volumes'][1]['secret'],{'secretName':'prod-nas-nfs-ssh','defaultMode':288})
        self.assertTrue(pod['containers'][0]['securityContext']['readOnlyRootFilesystem'])
        policy=d['NetworkPolicy/prod-nas-nfs-restricted']['spec']
        self.assertEqual(policy['egress'],[{'to':[{'ipBlock':{'cidr':'192.0.2.10/32'}}],'ports':[{'port':22,'protocol':'TCP'}]}])
        self.assertEqual(policy['ingress'][0]['ports'],[{'port':9189,'protocol':'TCP'}])

    def test_opt_in_validation(self):
        for image in ['latest', 'registry.example/collector:latest']:
            with self.assertRaises(ValueError):self.render(image)
        with self.assertRaises(ValueError):terraform_manifest(nas_nfs_image=IMAGE)
        self.assertIn('[30m]',rules()['groups'][0]['rules'][0]['expr'])
        self.assertEqual(rules()['groups'][0]['rules'][1]['for'],'10m')
