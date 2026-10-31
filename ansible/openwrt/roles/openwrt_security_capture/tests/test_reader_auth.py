#!/usr/bin/env python3
"""Run actual Ansible blockinfile against a disposable synthetic key file."""
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
    tasks=yaml.safe_load((ROOT/'tasks/main.yml').read_text())
    input_task=next(t for t in tasks if t['name']=='Security capture reader 公開鍵の形式を検証')
    writer=copy.deepcopy(next(t for t in tasks if 'ansible.builtin.blockinfile' in t))
    executable=Path(sys.executable).with_name('ansible-playbook')
    if not executable.exists():
        executable=Path(shutil.which('ansible-playbook') or '/absent-ansible')
    if not executable.is_file():
        raise RuntimeError('ansible-playbook required')
    blob=struct.pack('>I',11)+b'ssh-ed25519'+struct.pack('>I',32)+bytes(range(32))
    public='ssh-ed25519 '+base64.b64encode(blob).decode()+' fixture'
    with tempfile.TemporaryDirectory(prefix='capture-reader-auth-') as name:
        root=Path(name); auth=root/'authorized_keys'; original=b'# keep admin key\nssh-ed25519 fixture-existing admin\n'
        auth.write_bytes(original);auth.chmod(0o600)
        # The writer is unchanged except for its disposable path and the test
        # account's numeric owner; production still fixes root:/etc/dropbear.
        module=writer['ansible.builtin.blockinfile'];module.update(path=str(auth),owner=str(os.getuid()),group=str(os.getgid()))
        play=root/'play.yml';variables=root/'vars.json'
        play.write_text(yaml.safe_dump([{'hosts':'all','gather_facts':False,'tasks':[input_task,writer]}],allow_unicode=True))
        def run(enabled=True, marker=1, check=False, key=public, expected=0):
            variables.write_text(json.dumps({'openwrt_security_capture_enabled':enabled,
                'openwrt_security_capture_reader_public_key':key,
                'openwrt_security_capture_reader_keys':{'stat':{'exists':True}},
                'openwrt_security_capture_reader_marker':{'rc':marker}}))
            args=[str(executable),'-i','localhost,','-c','local',str(play),'-e','@'+str(variables)]
            if check:args.append('--check')
            result=subprocess.run(args,text=True,capture_output=True,timeout=45,
                                  env=dict(os.environ,ANSIBLE_NOCOLOR='1',ANSIBLE_LOCAL_TEMP=str(root/'ansible-tmp')))
            if result.returncode!=expected:
                raise AssertionError('Ansible fixture failed: '+result.stdout[-2000:]+result.stderr[-1000:])
            return result.stdout
        run(check=True);assert auth.read_bytes()==original
        first=run();assert re.search(r'changed=1\b',first)
        text=auth.read_text();assert text.startswith(original.decode())
        assert 'no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,command="/usr/bin/python3 /usr/libexec/homecluster-security-capture.py ssh-export" '+public in text
        second=run(marker=0);assert re.search(r'changed=0\b',second)
        before=auth.read_bytes()
        run(marker=0,key=public+'\nssh-ed25519 injected',expected=2);assert auth.read_bytes()==before
        run(enabled=False,marker=0);assert auth.read_bytes()==original
        run(enabled=False,marker=1);assert auth.read_bytes()==original
        auth.chmod(0o644)
        run(enabled=False,marker=1,key='');assert auth.read_bytes()==original and auth.stat().st_mode & 0o777==0o644
        print(json.dumps({'status':'pass','check_mode_no_write':True,'other_admin_keys_preserved':True,
                          'idempotent':True,'newline_rejected':True,'disabled_only_removes_own_block':True}))


if __name__=='__main__':
    main()
