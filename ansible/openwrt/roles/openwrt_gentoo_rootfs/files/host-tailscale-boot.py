#!/usr/bin/env python3
"""Use the already fetched boot revision, under the common ansible checkout lock."""
import os
from pathlib import Path
import re
import subprocess


def main():
    stage = os.environ.get('STAGE', '')
    overlay = os.environ.get('OVERLAY_ID', '')
    if stage not in ('stg', 'prod') or not re.fullmatch(r'[A-Za-z0-9_-]+', overlay):
        print('host_tailscale_boot_identity_invalid')
        return 1
    config = Path('/etc/ansible-pull/client_vars') / overlay / 'host_tailscale.yml'
    if not config.is_file():
        print('host_tailscale_not_opted_in')
        return 0
    source = Path('/var/lib/ansible-pull/ansible/arm64')
    playbook = source / 'playbooks/host-tailscale.yml'
    if not playbook.is_file():
        print('host_tailscale_source_unavailable')
        return 1
    env = dict(os.environ, ANSIBLE_CONFIG=str(source / 'ansible.cfg'),
               ANSIBLE_ROLES_PATH=str(source / 'roles'))
    # No git update, Galaxy download, inventory secrets or enrollment here.
    try:
        return subprocess.run([
            '/usr/bin/ansible-playbook', '-i', 'localhost,', '-c', 'local',
            '--limit', 'localhost', '-e', f'@{config}', '-e', f'stage={stage}',
            str(playbook),
        ], env=env, timeout=180).returncode
    except subprocess.TimeoutExpired:
        print('host_tailscale_setup_timeout')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
