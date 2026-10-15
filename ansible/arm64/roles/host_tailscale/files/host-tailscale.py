#!/usr/bin/env python3
"""Fixed host state guard; never print daemon output, preferences or credentials."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess

MOUNT = Path('/var/lib/rancher/k3s')
STATE = MOUNT / '.homecluster-host-state/tailscale'
CONFIG = Path('/etc/homecluster/host-tailscale.json')


class Refused(RuntimeError):
    pass


def check_mount(mountinfo: str, device_stat: os.stat_result) -> None:
    if not stat.S_ISBLK(device_stat.st_mode):
        raise Refused('expected_device_not_block')
    expected = f'{os.major(device_stat.st_rdev)}:{os.minor(device_stat.st_rdev)}'
    rows = [line.split() for line in mountinfo.splitlines()]
    rows = [r for r in rows if len(r) > 9 and r[4] == str(MOUNT)]
    if len(rows) != 1:
        raise Refused('dedicated_mount_missing_or_ambiguous')
    r = rows[0]
    try:
        fs = r[r.index('-') + 1]
    except (ValueError, IndexError):
        raise Refused('mount_record_invalid') from None
    if r[2] != expected or r[3] != '/' or fs != 'ext4' or 'rw' not in r[5].split(','):
        raise Refused('dedicated_mount_mismatch')


def guard(config: dict) -> None:
    device = config.get('device', '')
    if not re.fullmatch(r'/dev/[A-Za-z0-9_./:+-]+', device):
        raise Refused('device_invalid')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', config.get('hostname', '')):
        raise Refused('hostname_invalid')
    if MOUNT.is_symlink() or MOUNT.resolve() != MOUNT:
        raise Refused('mount_path_symlink')
    check_mount(Path('/proc/self/mountinfo').read_text(), os.stat(device))


def private_dirs(root: Path, paths: list[Path]) -> bool:
    changed = False
    for p in paths:
        try:
            p.mkdir(mode=0o700)
            changed = True
        except FileExistsError:
            pass
        info = p.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_dev != root.stat().st_dev:
            raise Refused('state_directory_unsafe')
        if stat.S_IMODE(info.st_mode) != 0o700:
            p.chmod(0o700)
            changed = True
    state_file = paths[-1] / 'tailscaled.state'
    if state_file.exists() or state_file.is_symlink():
        info = state_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise Refused('state_file_unsafe')
        if stat.S_IMODE(info.st_mode) != 0o600:
            state_file.chmod(0o600)
            changed = True
    return changed


def backend_state() -> str:
    r = subprocess.run(['/usr/bin/tailscale', 'status', '--json'], capture_output=True, text=True, timeout=15)
    if r.returncode not in (0, 1):
        raise Refused('daemon_status_failed')
    # tailscale status may return nonzero while logged out. Require valid JSON.
    try:
        state = json.loads(r.stdout)['BackendState']
    except (ValueError, KeyError, TypeError):
        raise Refused('daemon_state_unknown') from None
    if state not in ('Running', 'NeedsLogin', 'NoState'):
        raise Refused('daemon_state_not_enrollable')
    if state == 'Running' and r.returncode:
        raise Refused('daemon_status_failed')
    return state


def enroll(config: dict, key_file: Path) -> bool:
    guard(config)
    if backend_state() == 'Running':
        return False
    info = key_file.lstat()
    if (key_file.parent != Path('/run') or not key_file.name.startswith('host-tailscale-auth-')
            or not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size == 0):
        raise Refused('auth_file_unsafe')
    r = subprocess.run([
        '/usr/bin/tailscale', 'up', f'--auth-key=file:{key_file}',
        '--accept-dns=false', '--accept-routes=false', '--ssh=false',
        '--advertise-routes=', '--advertise-exit-node=false', '--exit-node=',
        f'--hostname={config["hostname"]}', '--timeout=60s',
    ], capture_output=True, timeout=75)
    if r.returncode or backend_state() != 'Running':
        raise Refused('enrollment_failed')
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'status', 'enroll'])
    parser.add_argument('--key-file', type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(CONFIG.read_text())
        if not isinstance(config, dict):
            raise Refused('host_config_invalid')
        guard(config)
        changed = False
        if args.action == 'prepare':
            if not stat.S_ISCHR(Path('/dev/net/tun').stat().st_mode):
                raise Refused('tun_unavailable')
            changed = private_dirs(MOUNT, [STATE.parent, STATE])
            guard(config)
        elif args.action == 'status':
            print(json.dumps({'backend_state': backend_state()}))
            return 0
        else:
            if args.key_file is None:
                raise Refused('auth_file_required')
            changed = enroll(config, args.key_file)
        print(json.dumps({'changed': changed}))
        return 0
    except Refused as exc:
        print(json.dumps({'error': str(exc)}))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print(json.dumps({'error': 'host_tailscale_precondition_failed'}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
