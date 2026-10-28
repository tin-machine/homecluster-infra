#!/usr/bin/env python3
"""Render the real kernel worker/slot/jobs handoff with synthetic inventory."""
import unittest
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'ansible/arm64/roles/rpi5_common_kernel_build'


class KernelDistccContract(unittest.TestCase):
    def setUp(self):
        self.loader = DataLoader()
        self.tasks = self.loader.load_from_file(str(ROLE / 'tasks/main.yml'))
        self.variables = dict(self.loader.load_from_file(str(ROLE / 'defaults/main.yml')))
        self.variables.update(
            inventory_hostname='builder',
            groups={'all': ['builder', 'pi4-worker', 'pi5-a', 'pi5-b', 'prod-worker', 'disabled']},
            hostvars={
                'builder': {'distcc': {'enabled': True, 'slots': 3}},
                'pi4-worker': {'ansible_host': '192.0.2.1', 'distcc': {'enabled': True, 'slots': 2}},
                'pi5-a': {'ansible_host': '192.0.2.2', 'distcc': {'enabled': True, 'slots': 5}},
                'pi5-b': {'ansible_host': '192.0.2.3', 'distcc': {'enabled': True, 'slots': 5}},
                'prod-worker': {'ansible_host': '192.0.2.4', 'distcc': {'enabled': True, 'client_enabled': False, 'slots': 2}},
                'disabled': {'distcc': {'enabled': False}},
            },
        )

    def render(self, value):
        return Templar(loader=self.loader, variables=self.variables).template(trust_as_template(str(value)))

    def fact(self, key):
        task = next(t for t in self.tasks if key in t.get('ansible.builtin.set_fact', {}))
        value = self.render(task['ansible.builtin.set_fact'][key])
        self.variables[key] = value
        return value

    def test_prod_worker_does_not_require_its_own_portage_client(self):
        workers = self.fact('rpi5_common_kernel_build_distcc_worker_names')
        self.assertEqual(workers, ['pi4-worker', 'pi5-a', 'pi5-b', 'prod-worker'])
        hosts = self.fact('rpi5_common_kernel_build_distcc_hosts_effective')
        self.assertEqual(hosts, '192.0.2.1/2 192.0.2.2/5 192.0.2.3/5 192.0.2.4/2 localhost/2 --localslots=2 --localslots_cpp=4')
        probe = next(t for t in self.tasks if t.get('register') == 'rpi5_common_kernel_build_distcc_preflight')
        self.assertEqual(self.render(probe['loop']), workers)
        jobs = next(t for t in self.tasks if t.get('register') == 'rpi5_common_kernel_build_distcc_jobs')
        self.assertEqual(jobs['ansible.builtin.command']['argv'], ['distcc', '-j'])
        self.assertEqual(self.render(jobs['environment']['DISTCC_HOSTS']), hosts)

    def test_explicit_worker_opt_out_and_custom_port(self):
        self.variables['hostvars']['prod-worker']['distcc']['kernel_worker_enabled'] = False
        self.variables['hostvars']['pi5-a']['distcc']['port'] = 3633
        self.assertNotIn('prod-worker', self.fact('rpi5_common_kernel_build_distcc_worker_names'))
        hosts = self.fact('rpi5_common_kernel_build_distcc_hosts_effective')
        self.assertIn('192.0.2.2:3633/5', hosts)
        self.assertNotIn('192.0.2.4', hosts)

    def test_computed_jobs_flow_into_actual_make_command(self):
        self.variables['rpi5_common_kernel_build_distcc_jobs'] = {'stdout': '16\n'}
        self.variables.update(rpi5_common_kernel_build_stage_date='20261107',
                              rpi5_common_kernel_build_distcc_hosts_effective='localhost/2')
        self.assertEqual(self.fact('rpi5_common_kernel_build_make_jobs_effective'), 16)
        build = next(t for t in self.tasks if t.get('register') == 'rpi5_common_kernel_build_kernel_timing')
        self.assertIn('make -j16 ', self.render(build['ansible.builtin.shell']))
        self.variables['rpi5_common_kernel_build_make_jobs_cap'] = 12
        self.assertEqual(self.fact('rpi5_common_kernel_build_make_jobs_effective'), 12)
        self.variables['rpi5_common_kernel_build_make_jobs'] = 10
        self.assertEqual(self.fact('rpi5_common_kernel_build_make_jobs_effective'), 10)

    def test_invalid_distcc_result_is_not_used(self):
        gate = next(t for t in self.tasks if t['name'] == 'distccの計算値を検証')
        for result in ('', '0', '-1', 'nan', '4 hosts', '1025'):
            with self.subTest(result=result):
                self.variables['rpi5_common_kernel_build_distcc_jobs'] = {'stdout': result}
                self.assertFalse(all(self.render('{{ ' + str(condition) + ' }}')
                                     for condition in gate['ansible.builtin.assert']['that']))

    def test_readiness_precedes_source_build_and_outer_cannot_fallback(self):
        probes = [i for i, t in enumerate(self.tasks) if 'distcc_preflight' in str(t) or 'ansible.builtin.wait_for' in t]
        source = next(i for i, t in enumerate(self.tasks) if 'ansible.builtin.git' in t)
        self.assertLess(max(probes), source)
        outer = self.loader.load_from_file(str(ROOT / 'ansible/arm64/playbooks/rpi5-egpu-nvidia-artifact-bundle.yml'))
        self.assertFalse(any('rpi5_common_kernel_build_distcc_enabled' in t.get('ansible.builtin.set_fact', {})
                             for t in outer[0]['tasks']))


if __name__ == '__main__':
    unittest.main()
