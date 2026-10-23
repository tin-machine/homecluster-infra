#!/usr/bin/env python3
"""Offline behavior and source boundary fixtures for optional host Tailscale."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'ansible/arm64/roles/host_tailscale'
PXE = ROOT / 'ansible/openwrt/roles/openwrt_gentoo_rootfs'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('host_tailscale_guard', ROLE / 'files/host-tailscale.py')
boot = load('host_tailscale_boot', PXE / 'files/host-tailscale-boot.py')
filters = load('host_tailscale_filters', PXE / 'filter_plugins/host_tailscale_boot_vars.py')
catalog = load('catalog', ROOT / 'ansible/openwrt/roles/openwrt_pxe_client_catalog/filter_plugins/openwrt_pxe_client_catalog.py')
MOUNT_ROW = '40 20 8:1 / /var/lib/rancher/k3s rw,relatime - ext4 /dev/sda1 rw\n'
DEVICE = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(8, 1))


class MountTests(unittest.TestCase):
    def test_exact_device_accepts(self):
        guard.check_mount(MOUNT_ROW, DEVICE)

    def test_absent_overlay_wrong_device_readonly_bind_and_duplicate_refuse(self):
        for row in ['', MOUNT_ROW.replace('ext4', 'overlay'), MOUNT_ROW.replace('8:1', '8:2'),
                    MOUNT_ROW.replace('rw,relatime', 'ro,relatime'),
                    MOUNT_ROW.replace('8:1 / ', '8:1 /other '), MOUNT_ROW * 2]:
            with self.subTest(row=row), self.assertRaises(guard.Refused):
                guard.check_mount(row, DEVICE)

    def test_regular_file_device_refuses(self):
        with self.assertRaises(guard.Refused):
            guard.check_mount(MOUNT_ROW, SimpleNamespace(st_mode=stat.S_IFREG, st_rdev=0))

    def test_no_state_creation_before_mount_gate(self):
        with patch.object(guard.CONFIG.__class__, 'read_text', return_value='{}'), \
                patch.object(guard, 'guard', side_effect=guard.Refused('dedicated_mount_missing_or_ambiguous')), \
                patch.object(guard, 'private_dirs') as mkdir, patch('sys.argv', ['guard', 'prepare']), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(guard.main(), 1)
            mkdir.assert_not_called()

    def test_state_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'outside').mkdir()
            (root / 'state').symlink_to(root / 'outside', target_is_directory=True)
            with self.assertRaises(guard.Refused):
                guard.private_dirs(root, [root / 'state'])
            self.assertEqual(list((root / 'outside').iterdir()), [])


class EnrollmentTests(unittest.TestCase):
    def test_backend_state_is_bounded(self):
        for state, rc in [('Running', 0), ('NeedsLogin', 1), ('NoState', 1)]:
            with patch.object(guard.subprocess, 'run', return_value=SimpleNamespace(returncode=rc, stdout=json.dumps({'BackendState': state, 'Peer': {'private': 'ignored'}}))):
                self.assertEqual(guard.backend_state(), state)

    def test_unknown_malformed_and_failed_running_refuse(self):
        for output, rc in [('{}', 0), ('not-json', 1), ('{"BackendState":"Stopped"}', 0),
                           ('{"BackendState":"Running"}', 1), ('{"BackendState":"NoState"}', 2)]:
            with patch.object(guard.subprocess, 'run', return_value=SimpleNamespace(returncode=rc, stdout=output)), self.assertRaises(guard.Refused):
                guard.backend_state()

    def test_running_skips_even_missing_key(self):
        with patch.object(guard, 'guard'), patch.object(guard, 'backend_state', return_value='Running'), patch.object(guard.subprocess, 'run') as run:
            self.assertFalse(guard.enroll({}, Path('/does-not-exist')))
            run.assert_not_called()

    def test_login_uses_file_only_and_preserves_lan_preferences(self):
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1, st_size=20)
        with patch.object(guard, 'guard'), patch.object(guard, 'backend_state', side_effect=['NeedsLogin', 'Running']), \
                patch.object(Path, 'lstat', return_value=info), patch.object(guard.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(guard.enroll({'hostname': 'fixture-agent'}, Path('/run/host-tailscale-auth-fixture')))
            argv = run.call_args.args[0]
            for flag in ['--accept-dns=false', '--accept-routes=false', '--ssh=false', '--exit-node=', '--advertise-routes=', '--advertise-exit-node=false']:
                self.assertIn(flag, argv)
            self.assertIn('--auth-key=file:/run/host-tailscale-auth-fixture', argv)

    def test_failed_enrollment_does_not_report_success(self):
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1, st_size=20)
        with patch.object(guard, 'guard'), patch.object(guard, 'backend_state', return_value='NeedsLogin'), \
                patch.object(Path, 'lstat', return_value=info), patch.object(guard.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), self.assertRaisesRegex(guard.Refused, '^enrollment_failed$'):
            guard.enroll({'hostname': 'fixture-agent'}, Path('/run/host-tailscale-auth-fixture'))

    def test_world_readable_or_symlink_key_refused(self):
        for mode in [stat.S_IFREG | 0o644, stat.S_IFLNK | 0o777]:
            with patch.object(guard, 'guard'), patch.object(guard, 'backend_state', return_value='NeedsLogin'), \
                    patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=mode, st_uid=0, st_nlink=1, st_size=20)), \
                    patch.object(guard.subprocess, 'run') as run:
                with self.assertRaises(guard.Refused):
                    guard.enroll({}, Path('/run/host-tailscale-auth-fixture'))
                run.assert_not_called()


class BootTests(unittest.TestCase):
    def test_staging_build_includes_tailscale_without_enabling_production(self):
        playbooks = ROOT / 'ansible/openwrt/playbooks'
        staging = yaml.safe_load((playbooks / 'pxe-release-bundle-staging.yml').read_text())[0]
        build = yaml.safe_load((playbooks / 'pxe-release-bundle-build.yml').read_text())[0]
        common_kernel = yaml.safe_load((playbooks / 'pxe-release-bundle-staging-with-common-kernel.yml').read_text())
        self.assertEqual(staging['vars']['pxe_release_bundle_stage'], 'stg')
        self.assertIs(staging['vars']['openwrt_gentoo_host_tailscale_enabled'], True)
        self.assertEqual(common_kernel[-1]['ansible.builtin.import_playbook'], 'pxe-release-bundle-build.yml')
        self.assertEqual(common_kernel[-1]['vars']['pxe_release_bundle_stage'], 'stg')
        enabled = Environment().from_string(build['vars']['openwrt_gentoo_host_tailscale_enabled'])
        self.assertEqual(enabled.render(pxe_release_bundle_stage='stg'), 'True')
        self.assertEqual(enabled.render(pxe_release_bundle_stage='prod'), 'False')
        self.assertEqual(enabled.render(), 'False')

    def test_no_opt_in_does_not_start_ansible(self):
        with patch.dict(os.environ, {'STAGE': 'stg', 'OVERLAY_ID': 'fixture-agent'}), patch.object(Path, 'is_file', return_value=False), patch.object(boot.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(boot.main(), 0)
            run.assert_not_called()

    def test_bad_identity_refuses(self):
        for stage, overlay in [('unknown', 'agent'), ('prod', '../other')]:
            with patch.dict(os.environ, {'STAGE': stage, 'OVERLAY_ID': overlay}), patch.object(boot.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(boot.main(), 1)
                run.assert_not_called()

    def test_boot_uses_only_configure_play_and_existing_source(self):
        with patch.dict(os.environ, {'STAGE': 'prod', 'OVERLAY_ID': 'fixture-cm'}), patch.object(Path, 'is_file', return_value=True), patch.object(boot.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(boot.main(), 0)
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], '/usr/bin/ansible-playbook')
            self.assertTrue(argv[-1].endswith('/playbooks/host-tailscale.yml'))
            self.assertIn('stage=prod', argv)
            self.assertNotIn('host_tailscale_enroll', ' '.join(argv))

    def test_no_enrollment_in_normal_play(self):
        self.assertNotIn('host-tailscale-enroll', (ROOT / 'ansible/arm64/site.yml').read_text())
        self.assertNotIn('auth_key', (ROOT / 'ansible/arm64/playbooks/host-tailscale.yml').read_text())
        defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        self.assertIs(defaults['host_tailscale_enabled'], False)

    def test_no_failure_dependency_into_k3s_chain(self):
        unit = (PXE / 'files/homecluster-host-tailscale.service').read_text()
        active = '\n'.join(line for line in unit.splitlines() if not line.startswith('#'))
        self.assertNotIn('OnSuccess=', active)
        self.assertNotIn('Requires=', active)
        daemon = (ROLE / 'files/tailscaled.service').read_text()
        self.assertIn('BindsTo=var-lib-rancher-k3s.mount', daemon)
        self.assertIn('ConditionPathIsMountPoint=/var/lib/rancher/k3s', daemon)
        self.assertNotIn('StateDirectory=', daemon)


class SecretBoundaryTests(unittest.TestCase):
    def test_recursive_credentials_are_rejected(self):
        for value in [{'host_tailscale_auth_key': 'fixture'}, {'x': [{'vars': {'host_tailscale_auth_key': ''}}]}]:
            self.assertFalse(filters.host_tailscale_boot_vars_safe(value))
        self.assertTrue(filters.host_tailscale_boot_vars_safe({'host_tailscale_enabled': True}))

    def test_catalog_whitelists_public_fields(self):
        hv = {
            'fixture': {
                'stage': 'stg', 'host_tailscale_enabled': True,
                'host_tailscale_hostname': 'fixture-agent', 'host_tailscale_auth_key': 'fixture-private',
                'k3s_local_storage_enabled': True, 'k3s_local_storage_device': '/dev/disk/by-label/fixture',
                'openwrt_pxe_client': {'enabled': True, 'router': 'router', 'roles': ['base'], 'overlay': {'id': 'fixture'}},
            }
        }
        result = catalog.build_openwrt_pxe_client_catalog(hv, {}, 'router')
        text = json.dumps(result)
        self.assertNotIn('fixture-private', text)
        rows = result['ansible_pull_client_vars']
        cfg = next(r['vars'] for r in rows if r['role'] == 'host_tailscale')
        self.assertEqual(set(cfg), {'host_tailscale_enabled', 'host_tailscale_hostname', 'host_tailscale_storage_device'})
        hv['fixture']['k3s_local_storage_force_format'] = True
        with self.assertRaises(ValueError):
            catalog.build_openwrt_pxe_client_catalog(hv, {}, 'router')
        hv['fixture']['k3s_local_storage_force_format'] = False
        hv['fixture']['openwrt_pxe_client']['ansible_pull'] = {
            'extra_vars': {'k3s_stg_storage': {'k3s_local_storage_force_format': True}}}
        with self.assertRaises(ValueError):
            catalog.build_openwrt_pxe_client_catalog(hv, {}, 'router')

    def test_enrollment_cleanup_and_explicit_gates_exist(self):
        play = yaml.safe_load((ROOT / 'ansible/arm64/playbooks/host-tailscale-enroll.yml').read_text())[0]
        self.assertEqual(play['tags'], ['never', 'host_tailscale_enroll'])
        gate = play['tasks'][0]['ansible.builtin.assert']['that']
        self.assertIn('ansible_limit | default(\'\') == inventory_hostname', gate)
        self.assertIn("'host_tailscale_enroll' in ansible_run_tags", gate)
        block = play['tasks'][2]
        self.assertIn('always', block)
        self.assertEqual(block['always'][0]['ansible.builtin.file']['state'], 'absent')
        for task in block['block']:
            self.assertTrue(task.get('no_log'))


@unittest.skipUnless(shutil.which('ansible-playbook'), 'Ansible is required for integration fixtures')
class AnsibleTests(unittest.TestCase):
    def run_play(self, args):
        env = dict(os.environ, ANSIBLE_NOCOLOR='1', ANSIBLE_ROLES_PATH=f'{ROOT}/ansible/arm64/roles:{ROOT}/ansible/openwrt/roles')
        return subprocess.run(['ansible-playbook', *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=90)

    def test_default_off_has_no_runtime_changes(self):
        r = self.run_play(['-i', 'localhost,', '-c', 'local', 'ansible/arm64/playbooks/host-tailscale.yml'])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r'changed=0\s+')

    def test_enrollment_tagless_skips_and_local_wildcard_is_refused(self):
        play = 'ansible/arm64/playbooks/host-tailscale-enroll.yml'
        r = self.run_play(['-i', 'localhost,', '-c', 'local', play, '--list-tasks'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('Read only the bounded backend state', r.stdout)
        r = self.run_play(['-i', 'localhost,', '-c', 'local', play, '--tags', 'host_tailscale_enroll', '--limit', 'all'])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('TASK [Read only the bounded backend state]', r.stdout)

    def test_lower_rootfs_tagged_install_is_idempotent_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            lower = Path(temp) / 'lower'
            (lower / 'etc/systemd/system/multi-user.target.wants').mkdir(parents=True)
            play = Path(temp) / 'fixture.yml'
            body = [{
                'hosts': 'all', 'gather_facts': False,
                'vars': {'openwrt_gentoo_host_tailscale_enabled': True,
                         'openwrt_gentoo_homecluster_unit_chain_enabled': True,
                         'openwrt_gentoo_homecluster_unit_chain_start_enabled': True,
                         'openwrt_gentoo_ansible_pull_rootfs_dirs': [str(lower)]},
                'tasks': [{'ansible.builtin.include_role': {
                    'name': 'openwrt_gentoo_rootfs', 'tasks_from': 'host_tailscale.yml',
                    'apply': {'tags': ['pxe_ansible_pull']}}, 'tags': ['pxe_ansible_pull']}],
            }]
            play.write_text(yaml.safe_dump(body))
            args = ['-i', 'localhost,', '-c', 'local', str(play), '--tags', 'pxe_ansible_pull']
            r = self.run_play(args)
            self.assertNotEqual(r.returncode, 0)
            for binary in ['usr/bin/tailscale', 'usr/sbin/tailscaled']:
                path = lower / binary
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            r = self.run_play(args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            unit = lower / 'etc/systemd/system/homecluster-storage.service.wants/homecluster-host-tailscale.service'
            self.assertTrue(unit.is_file())
            r = self.run_play(args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertRegex(r.stdout, r'changed=0\s+')
            body[0]['vars']['openwrt_gentoo_host_tailscale_enabled'] = False
            play.write_text(yaml.safe_dump(body))
            r = self.run_play(args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(unit.exists())

    def test_px_runtime_rejects_key_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            play = Path(temp) / 'fixture.yml'
            body = [{'hosts': 'all', 'gather_facts': False,
                     'vars': {'openwrt_gentoo_ansible_pull_client_vars_effective': [
                         {'vars': {'host_tailscale_auth_key': 'fixture-private-value'}}]},
                     'tasks': [{'ansible.builtin.import_role': {'name': 'openwrt_gentoo_rootfs', 'tasks_from': 'pxe_runtime.yml'}}]}]
            play.write_text(yaml.safe_dump(body))
            r = self.run_play(['-i', 'localhost,', '-c', 'local', str(play), '--tags', 'fixture-only'])
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn('fixture-private-value', r.stdout + r.stderr)
            self.assertNotIn('TASK [openwrt_gentoo_rootfs : Configure optional', r.stdout)


if __name__ == '__main__':
    unittest.main()
