#!/usr/bin/env python3
"""Exercise paired selector producer through the real host release consumer."""
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

ROOT = Path(__file__).resolve().parents[2]
LOADER = importlib.machinery.SourceFileLoader('fleet_rollout_contract', str(ROOT / 'scripts/pi-rpi5-common-kernel-rollout'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
ROLLOUT = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(ROLLOUT)


class PairedFleetContract(unittest.TestCase):
    def setUp(self):
        self.loader = DataLoader()
        self.tasks = self.loader.load_from_file(str(ROOT / 'ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml'))[0]['tasks']
        self.variables = {
            'groups': {'env_stg': ['rpi4-server', 'rpi5-a', 'rpi5-b', 'rpi5-c'],
                       'env_prod': ['prod-worker'], 'k3s_stg_server': ['rpi4-server'],
                       'k3s_stg_agents': ['rpi5-a', 'rpi5-b', 'rpi5-c']},
            'homecluster_common_kernel_rollout_targets': ['rpi4-server', 'rpi5-a', 'rpi5-b', 'rpi5-c'],
            'homecluster_common_kernel_rollout_phase': 'full_fleet',
            'homecluster_common_kernel_accepted_selector': '20261107-rpi5',
            'openwrt_pxe_hosts_effective': [
                {'name': name, 'tftp_stage': stage, 'tftp_release': release, 'rootfs_release': release}
                for name, stage, release in [('rpi4-server', 'stg', '20260101-rpi4'),
                    ('rpi5-a', 'stg', '20260101-rpi5'), ('rpi5-b', 'stg', '20260101-rpi5'),
                    ('rpi5-c', 'stg', '20260101-rpi5'), ('prod-worker', 'prod', '20260101-prod-rpi5')]
            ],
        }

    def render(self, value):
        return Templar(loader=self.loader, variables=self.variables).template(trust_as_template(str(value)))

    def fact(self, key):
        task = next(t for t in self.tasks if key in t.get('ansible.builtin.set_fact', {}))
        result = self.render(task['ansible.builtin.set_fact'][key])
        self.variables[key] = result
        return result

    def test_old_pi4_override_is_replaced_and_prod_never_reaches_mutation(self):
        selectors = self.fact('homecluster_common_kernel_applied_selectors')
        self.assertEqual(selectors['rpi4-server']['tftp_release'], '20261107-rpi4')
        selected = self.fact('openwrt_pxe_hosts_effective_override')
        self.assertEqual([h['name'] for h in selected], self.variables['homecluster_common_kernel_rollout_targets'])
        tasks = self.loader.load_from_file(str(ROOT / 'ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/pxe_host_releases.yml'))
        release = next(t for t in tasks if t['name'] == 'PXE host release を stage date から解決')
        self.variables.update(openwrt_gentoo_release_bundle_stage_dates={'stg': '20261107'},
            openwrt_gentoo_release_bundle_generations=[
                {'name': 'rpi4', 'host_name_regex': '^rpi4-', 'release_suffix': 'rpi4'},
                {'name': 'rpi5', 'host_name_regex': '^rpi5-', 'release_suffix': 'rpi5'}])
        # pxe_host_releases first installs the override as its effective list.
        self.variables['openwrt_pxe_hosts_effective'] = selected
        resolved = self.render(release['ansible.builtin.set_fact']['openwrt_pxe_hosts_effective'])
        self.assertEqual(len(resolved), 4)
        self.assertEqual(resolved[0]['rootfs_release'], '20261107-rpi4')
        self.assertEqual({h['rootfs_release'] for h in resolved[1:]}, {'20261107-rpi5'})
        self.assertNotIn('prod-worker', str(resolved))

    def test_target_gate_rejects_prod_even_when_marked_staging(self):
        gate = next(t for t in self.tasks if t['name'] == 'Pi5 common kernel rollout inputを検証')
        conditions = [str(x) for x in gate['ansible.builtin.assert']['that'] if "groups['env_" in str(x)]
        self.assertTrue(all(self.render('{{ ' + x + ' }}') for x in conditions))
        self.variables['homecluster_common_kernel_rollout_targets'].append('prod-worker')
        self.variables['groups']['env_stg'].append('prod-worker')
        self.assertFalse(all(self.render('{{ ' + x + ' }}') for x in conditions))

    def test_full_fleet_requires_exact_four_target_set(self):
        gate = self.tasks[0]['ansible.builtin.assert']['that']
        condition = next(str(x) for x in gate if "!= 'full_fleet'" in str(x))
        self.assertTrue(self.render('{{ ' + condition + ' }}'))
        self.variables['homecluster_common_kernel_rollout_targets'] = ['rpi5-a', 'rpi5-b', 'rpi5-c']
        self.assertFalse(self.render('{{ ' + condition + ' }}'))

    def test_inventory_fleet_includes_server_and_excludes_prod(self):
        groups = self.variables['groups']
        inventory = {name: {'hosts': hosts} for name, hosts in groups.items()}
        fleet = ROLLOUT.staging_fleet(inventory, groups['k3s_stg_agents'])
        self.assertEqual(fleet, groups['env_stg'])
        inventory['env_prod']['hosts'].append('rpi5-c')
        with self.assertRaises(ROLLOUT.RolloutError):
            ROLLOUT.staging_fleet(inventory, groups['k3s_stg_agents'])

    def test_rollback_restores_both_board_generations(self):
        self.variables['homecluster_common_kernel_rollout_phase'] = 'rollback_last_phase'
        old = {h['name']: {'tftp_release': h['tftp_release'], 'rootfs_release': h['rootfs_release']}
               for h in self.variables['openwrt_pxe_hosts_effective'] if h['tftp_stage'] == 'stg'}
        self.variables['homecluster_common_kernel_rollback_selectors'] = old
        self.assertEqual(self.fact('homecluster_common_kernel_applied_selectors'), old)


if __name__ == '__main__':
    unittest.main()
