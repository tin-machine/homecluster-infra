#!/usr/bin/env python3
"""Actual Ansible validation/blockinfile; only disposable synthetic keys/files."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    tasks = yaml.safe_load((ROOT / 'tasks/trigger.yml').read_text())
    check = tasks[0]
    writer = copy.deepcopy(next(t for t in tasks if 'ansible.builtin.blockinfile' in t))
    executable = Path(sys.executable).with_name('ansible-playbook')
    if not executable.exists():
        executable = Path(shutil.which('ansible-playbook') or '/absent-ansible')
    if not executable.is_file():
        raise RuntimeError('ansible-playbook required')
    def key(seed):
        blob = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(seed, seed + 32))
        return 'ssh-ed25519 ' + base64.b64encode(blob).decode() + ' fixture'
    public, reader = key(0), key(32)
    with tempfile.TemporaryDirectory(prefix='capture-trigger-auth-') as folder:
        root = Path(folder)
        auth = root / 'authorized_keys'
        original = ('# keep admin\nssh-ed25519 existing-admin\n# BEGIN HOMECLUSTER SECURITY CAPTURE READER\n'
                    'restrict,command="synthetic-reader" ' + reader + '\n# END HOMECLUSTER SECURITY CAPTURE READER\n').encode()
        auth.write_bytes(original); auth.chmod(0o600)
        writer['ansible.builtin.blockinfile'].update(path=str(auth), owner=str(os.getuid()), group=str(os.getgid()))
        play = root / 'play.yml'; variables = root / 'vars.json'
        play.write_text(yaml.safe_dump([{'hosts': 'all', 'gather_facts': False, 'tasks': [check, writer]}], allow_unicode=True))
        def run(enabled=True, marker=1, check_mode=False, key_value=public, expected=0):
            variables.write_text(json.dumps({'openwrt_security_capture_trigger_enabled': enabled,
                'openwrt_security_capture_trigger_public_key': key_value,
                'openwrt_security_capture_reader_public_key': reader,
                'openwrt_security_capture_reader_keys': {'stat': {'exists': True}},
                'openwrt_security_capture_trigger_marker': {'rc': marker}}))
            args = [str(executable), '-i', 'localhost,', '-c', 'local', str(play), '-e', '@' + str(variables)]
            if check_mode:
                args.append('--check')
            result = subprocess.run(args, text=True, capture_output=True, timeout=45,
                                    env=dict(os.environ, ANSIBLE_NOCOLOR='1', ANSIBLE_LOCAL_TEMP=str(root / 'ansible-tmp')))
            if result.returncode != expected:
                raise AssertionError(result.stdout[-2500:] + result.stderr[-1000:])
            return result.stdout
        run(check_mode=True); assert auth.read_bytes() == original
        assert re.search(r'changed=1\b', run())
        text = auth.read_text(); assert text.startswith(original.decode())
        assert 'no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,command="/usr/bin/python3 /usr/libexec/homecluster-security-trigger.py ssh" ' + public in text
        assert re.search(r'changed=0\b', run(marker=0))
        before = auth.read_bytes()
        run(marker=0, key_value=public + '\nextra-key', expected=2); assert auth.read_bytes() == before
        run(marker=0, key_value=reader, expected=2); assert auth.read_bytes() == before
        run(marker=0, key_value='', expected=2); assert auth.read_bytes() == before
        run(enabled=False, marker=0); assert auth.read_bytes() == original
        run(enabled=False, key_value=''); assert auth.read_bytes() == original
        print(json.dumps({'status': 'pass', 'check_mode_no_write': True, 'admin_and_reader_preserved': True,
                          'idempotent': True, 'newline_and_shared_key_rejected': True, 'disabled_own_block_only': True}))


if __name__ == '__main__':
    main()
