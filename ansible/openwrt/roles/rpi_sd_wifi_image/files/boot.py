#!/usr/bin/env python3
"""Fixed SD -> Wi-Fi -> DHCP -> NFS/overlay boot path, without shell evaluation."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

from wifi_config import ConfigError, atomic_write, parse_wifi_config, supplicant_config

STATE = Path('/run/sd-netboot-state')
BOOT = Path('/run/sd-netboot-boot')
LOWER = Path('/run/sd-netboot-lower')
OVERLAY = Path('/run/sd-netboot-overlay')
RUNTIME = Path('/run/sd-netboot-runtime')
PAYLOAD = Path('/usr/lib/sd-netboot')
STAGES = frozenset('sd_detected config_valid wifi_device_ready wifi_associated dhcp_bound root_selected root_mounted kernel_compatible switch_root_started userspace_ready workload_ready'.split())
TOKEN = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')
REASON = re.compile(r'^[a-z][a-z0-9_]{0,79}$')
REASONS = frozenset('ok config_too_large config_invalid_utf8 config_control_character config_unknown_key config_duplicate_key config_empty_value config_invalid_country config_invalid_ssid config_invalid_password config_missing_key prepare_failed wifi_device_ambiguous wifi_device_missing wifi_interface_invalid wifi_association_timeout dhcp_timeout dhcp_schema_invalid dhcp_option_encoding_invalid dhcp_option_duplicate dhcp_option_invalid root_or_role_missing root_path_invalid root_server_invalid role_schema_invalid role_value_invalid role_or_stage_invalid role_conflict root_mount_timeout_unknown root_mount_failed kernel_bundle_missing kernel_bundle_mismatch kernel_modules_missing command_failed root_setup_failed storage_daemon_missing boot_budget_exhausted'.split())
DEADLINE = None


def read_limited(path, limit):
    with Path(path).open('rb') as stream:
        return stream.read(limit)


class BootError(ValueError):
    pass


def run(argv, timeout=30, check=True):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=timeout, text=True, check=False)
    if check and result.returncode:
        raise BootError('command_failed')
    return result


def wait_for(function, seconds, reason):
    end = time.monotonic() + seconds
    if DEADLINE is not None:
        end=min(end,DEADLINE)
    while time.monotonic() < end:
        result = function()
        if result:
            return result
        time.sleep(1)
    raise BootError(reason)


class Diagnostics:
    """Only fixed fields; retain current and previous boot below 128 KiB total."""
    def __init__(self, directory, image_id, boot_id=None):
        self.directory = Path(directory)
        self.image_id = image_id
        self.boot_id = boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not TOKEN.fullmatch(image_id) or str(uuid.UUID(self.boot_id)) != self.boot_id:
            raise BootError('diagnostic_identity_invalid')
        self.sequence = 0
        self.events = []
        self.last_stage = 'sd_detected'
        self.io_failed = False

    def event(self, stage, result='pass', reason='ok'):
        if stage not in STAGES or result not in {'pass', 'fail', 'unknown'} or reason not in REASONS:
            raise BootError('diagnostic_schema_invalid')
        self.last_stage = stage
        self.sequence += 1
        event = {'image_id': self.image_id, 'boot_id': self.boot_id,
                 'monotonic_ms': int(time.monotonic()*1000), 'sequence': self.sequence,
                 'stage': stage, 'result': result, 'reason': reason}
        self.events.append(event)
        # One boot <= 60 KiB including summary, previous boot <= 60 KiB.
        while len(json.dumps(self.events).encode()) > 56000 and len(self.events) > 2:
            del self.events[1]
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            status = self.directory/'boot-status.txt'
            if status.exists():
                old = read_limited(status,4096).decode('utf-8',errors='replace')
                if f'boot_id={self.boot_id}\n' not in old:
                    for name in ['boot-status.txt', 'boot-events.jsonl']:
                        source = self.directory/name
                        if source.is_file():
                            atomic_write(self.directory/('previous-'+name), read_limited(source,58000))
            summary = ''.join(f'{key}={value}\n' for key, value in event.items())
            atomic_write(status, summary)
            atomic_write(self.directory/'boot-events.jsonl', ''.join(json.dumps(e)+'\n' for e in self.events))
            os.sync()
        except OSError:
            self.io_failed = True
        # Fixed reason only; never dump subprocess output/exception/input.
        print(f'sd-netboot stage={stage} result={result} reason={reason}', flush=True)


def option_bytes(option):
    data = option.get('data')
    if isinstance(data, list) and all(type(x) is int and 0 <= x <= 255 for x in data):
        return bytes(data)
    # networkctl v261 serializes DHCP option data as hex strings.
    if isinstance(data, str):
        if len(data)%2 == 0 and re.fullmatch('[0-9a-fA-F]*',data):
            return bytes.fromhex(data)
        try:
            return base64.b64decode(data, validate=True)
        except ValueError:
            pass
    raise BootError('dhcp_option_encoding_invalid')


def select_root(lease):
    """systemd >=261 networkctl dhcp-lease --json output; reject ambiguity."""
    options = lease.get('options')
    if not isinstance(options, list):
        raise BootError('dhcp_schema_invalid')
    selected = {}
    for option in options:
        if not isinstance(option, dict):
            raise BootError('dhcp_schema_invalid')
        tag = option.get('tag')
        if tag in (17, 224):
            if tag in selected:
                raise BootError('dhcp_option_duplicate')
            try:
                selected[tag] = option_bytes(option).decode('ascii')
            except UnicodeError:
                raise BootError('dhcp_option_invalid') from None
    if 17 not in selected or 224 not in selected:
        raise BootError('root_or_role_missing')
    match = re.fullmatch(r'(?:nfs4:)?([0-9.]+):(/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+)', selected[17])
    if not match:
        raise BootError('root_path_invalid')
    server, path = match.groups()
    try:
        ipaddress.IPv4Address(server)
    except ValueError:
        raise BootError('root_server_invalid') from None
    if any(part in {'.','..','latest'} for part in path.split('/')):
        raise BootError('root_path_invalid')
    # Delimiters are known key starts, not every comma (roles is a list).
    raw = selected[224]
    matches = list(re.finditer(r'(?:^|,)([a-z_]+)=', raw))
    if not matches or matches[0].start() != 0:
        raise BootError('role_schema_invalid')
    role = {}
    for index, item in enumerate(matches):
        key = item[1]
        value = raw[item.end():matches[index+1].start() if index+1 < len(matches) else len(raw)]
        if key not in {'role','roles','stage','overlay_id'} or key in role or not value:
            raise BootError('role_schema_invalid')
        if not all(TOKEN.fullmatch(v) for v in value.split(',')):
            raise BootError('role_value_invalid')
        role[key] = value
    if role.get('stage') != 'stg' or not role.get('overlay_id') or not (role.get('role') or role.get('roles')):
        raise BootError('role_or_stage_invalid')
    if role.get('role') and role.get('roles') and role['role'] not in role['roles'].split(','):
        raise BootError('role_conflict')
    return {'server':server, 'path':path, **role}


def verify_root(root, manifest):
    marker = root/'usr/lib/sd-netboot/kernel-bundle.json'
    try:
        stamp = json.loads(marker.read_text())
    except (OSError, ValueError):
        raise BootError('kernel_bundle_missing') from None
    for key in ['kernel_release','kernel_sha256','modules_sha256','boot_protocol_version']:
        if stamp.get(key) != manifest.get(key):
            raise BootError('kernel_bundle_mismatch')
    if not (root/'usr/lib/modules'/manifest['kernel_release']).is_dir():
        raise BootError('kernel_modules_missing')


def copy_runtime():
    shutil.copytree(PAYLOAD/'runtime', RUNTIME, symlinks=True)
    for directory in ['run/sd-netboot-state','run/systemd','run/udev','run/dbus','dev','proc','sys','tmp']:
        (RUNTIME/directory).mkdir(parents=True, exist_ok=True)
    for directory in ['/run/systemd/network','/run/udev','/run/dbus']:
        Path(directory).mkdir(parents=True, exist_ok=True)
    units = Path('/run/systemd/system')
    units.mkdir(parents=True, exist_ok=True)
    for path in (PAYLOAD/'units').glob('*.service'):
        shutil.copy2(path, units/path.name)
    # One networkd instance owns the boot link throughout boot and shutdown.
    (units/'systemd-networkd.service').symlink_to('sd-netboot-networkd.service')
    for name in ['systemd-networkd.socket','systemd-networkd-resolve-hook.socket']:
        (units/name).symlink_to('/dev/null')


def pin_mount(path, source, filesystem, options):
    """Keep initrd mounts across isolation; never put NFS under daemon bind paths."""
    name=str(path).strip('/').replace('-',r'\x2d').replace('/','-')+'.mount'
    content=('[Unit]\nDefaultDependencies=no\nIgnoreOnIsolate=yes\n'
             '[Mount]\nWhat='+source+'\nWhere='+str(path)+'\nType='+filesystem+'\nOptions='+options+'\n')
    atomic_write(Path('/run/systemd/system')/name,content,0o644)
    run(['systemctl','daemon-reload'])


def prepare():
    global DEADLINE
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    DEADLINE=time.monotonic()+600
    atomic_write(STATE/'deadline',str(DEADLINE))
    tokens = Path('/proc/cmdline').read_text().split()
    parts = [v.split('=',1)[1] for v in tokens if v.startswith('sd_netboot.partuuid=')]
    if len(parts) != 1 or not re.fullmatch(r'[0-9a-f]{8}-01',parts[0]):
        raise BootError('boot_partition_invalid')
    device = Path('/dev/disk/by-partuuid')/parts[0]
    wait_for(device.exists, 30, 'boot_partition_missing')
    devices=run(['blkid','-t','PARTUUID='+parts[0],'-o','device']).stdout.splitlines()
    if len(devices)!=1 or Path(devices[0]).resolve()!=device.resolve():
        raise BootError('boot_partition_ambiguous')
    boot = BOOT
    boot.mkdir()
    pin_mount(boot,str(device),'vfat','rw,nosuid,nodev,noexec,umask=0077')
    run(['mount','-t','vfat','-o','rw,nosuid,nodev,noexec,umask=0077',str(device),str(boot)])
    manifest = json.loads(read_limited(boot/'boot-manifest.json',1024*1024))
    if manifest.get('partuuid') != parts[0] or manifest.get('model') != 'pi4':
        raise BootError('boot_manifest_mismatch')
    atomic_write(STATE/'manifest.json', json.dumps(manifest))
    diag = Diagnostics(boot/'diagnostics',manifest['image_id'])
    diag.event('sd_detected')
    try:
        config = parse_wifi_config(read_limited(boot/'wifi.conf',4097))
        atomic_write(STATE/'wpa.conf',supplicant_config(config))
        run(['iw','reg','set',config['country']])
        diag.event('config_valid')
        run(['modprobe','brcmfmac'])
        def interfaces():
            found=[]
            for p in Path('/sys/class/net').glob('*'):
                if (p/'wireless').is_dir() and (p/'device/driver').resolve().name=='brcmfmac':
                    found.append(p.name)
            if len(found)>1:
                raise BootError('wifi_device_ambiguous')
            return found[0] if found else None
        interface=wait_for(interfaces,30,'wifi_device_missing')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}',interface):
            raise BootError('wifi_interface_invalid')
        mac=(Path('/sys/class/net')/interface/'address').read_text().strip()
        atomic_write(STATE/'interface',interface)
        atomic_write(STATE/'mac',mac)
        diag.event('wifi_device_ready')
        copy_runtime()
        network=(f'[Match]\nMACAddress={mac}\n\n[Link]\nRequiredForOnline=yes\n'
                 '[Network]\nDHCP=ipv4\nIPv6AcceptRA=no\nKeepConfiguration=yes\nIgnoreCarrierLoss=yes\n'
                 '[DHCPv4]\nClientIdentifier=mac\nRequestOptions=17 224\nSendRelease=no\n')
        atomic_write('/run/systemd/network/00-sd-netboot.network',network,0o644)
        atomic_write(STATE/'wpa-start','#!/bin/sh\nexec /usr/bin/wpa_supplicant -D nl80211 -i '+interface+' -c /run/sd-netboot-state/wpa.conf\n',0o700)
        # The same diagnostics object/sequence is reloaded by root().
        run(['systemctl','daemon-reload'])
        run(['systemctl','start','sd-netboot-wpa.service','sd-netboot-networkd.service'])
        for name,unit in [('wpa','sd-netboot-wpa.service'),('networkd','sd-netboot-networkd.service')]:
            pid=run(['systemctl','show','--property=MainPID','--value',unit]).stdout.strip()
            if not pid.isdecimal() or int(pid)<=1:
                raise BootError('storage_daemon_missing')
            atomic_write(STATE/(name+'.pid'),pid+'\n')
    except ConfigError as error:
        diag.event(diag.last_stage,'fail',error.reason)
        raise BootError(error.reason) from None
    except (BootError,OSError,ValueError,subprocess.SubprocessError):
        diag.event(diag.last_stage,'fail','prepare_failed')
        raise


def diagnostics():
    manifest=json.loads((STATE/'manifest.json').read_text())
    diag=Diagnostics(BOOT/'diagnostics',manifest['image_id'])
    try:
        previous=[json.loads(v) for v in (diag.directory/'boot-events.jsonl').read_text().splitlines()]
        diag.events=[e for e in previous if e.get('boot_id')==diag.boot_id]
        diag.sequence=max([e['sequence'] for e in diag.events],default=0)
        if diag.events:
            diag.last_stage=diag.events[-1]['stage']
    except (OSError,ValueError,KeyError):
        pass
    return diag,manifest


def root():
    global DEADLINE
    DEADLINE=float((STATE/'deadline').read_text())
    diag,manifest=diagnostics()
    try:
        interface=(STATE/'interface').read_text()
        def associated():
            return 'wpa_state=COMPLETED' in run(['wpa_cli','-p',str(STATE/'wpa'),'-i',interface,'status'],check=False).stdout.splitlines()
        wait_for(associated,90,'wifi_association_timeout')
        diag.event('wifi_associated')
        def lease():
            value=run(['networkctl','--json=short','dhcp-lease',interface],check=False)
            if value.returncode:
                return None
            try:
                return json.loads(value.stdout)
            except ValueError:
                return None
        bound=wait_for(lease,60,'dhcp_timeout')
        diag.event('dhcp_bound')
        selection=select_root(bound)
        atomic_write(STATE/'selection.json',json.dumps(selection))
        diag.event('root_selected')
        lower=LOWER
        lower.mkdir()
        pin_mount(lower,selection['server']+':'+selection['path'],'nfs4','ro,vers=4.2,proto=tcp,hard')
        process=subprocess.Popen(['mount','-t','nfs4','-o','ro,vers=4.2,proto=tcp,hard',selection['server']+':'+selection['path'],str(lower)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        end=min(time.monotonic()+120,DEADLINE)
        while process.poll() is None and time.monotonic()<end:
            time.sleep(1)
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            raise BootError('root_mount_timeout_unknown')
        if process.returncode:
            raise BootError('root_mount_failed')
        diag.event('root_mounted')
        verify_root(lower,manifest)
        diag.event('kernel_compatible')
        for name in ['upper','work']:
            (OVERLAY/name).mkdir(parents=True)
        Path('/sysroot').mkdir(exist_ok=True)
        run(['mount','-t','overlay','overlay','-o',f'lowerdir={lower},upperdir={OVERLAY}/upper,workdir={OVERLAY}/work','/sysroot'])
        # Preserve one immutable role selection for this boot; renewal never switches OS.
        role='roles='+selection.get('roles',selection.get('role',''))+'\nstage='+selection['stage']+'\noverlay_id='+selection['overlay_id']+'\n'
        atomic_write('/sysroot/etc/sd-netboot-role',role)
        atomic_write('/sysroot/etc/NetworkManager/conf.d/00-sd-netboot.conf',
                     '[keyfile]\nunmanaged-devices=mac:'+(STATE/'mac').read_text()+'\n',0o644)
        wants=Path('/sysroot/etc/systemd/system/multi-user.target.wants')
        wants.mkdir(parents=True,exist_ok=True)
        (wants/'sd-netboot-userspace.service').symlink_to('/run/systemd/system/sd-netboot-userspace.service')
        # dracut's shutdown image remains available independently of NFS.
        run(['cp','-a',str(PAYLOAD/'exitrd')+'/.','/run/initramfs'])
        atomic_write('/run/initramfs/.need_shutdown','')
        atomic_write('/run/initramfs/.sd-netboot','')
        diag.event('switch_root_started')
        Path('/run/sd-netboot-root-ready').touch()
    except (BootError,OSError,ValueError,subprocess.SubprocessError) as error:
        reason=str(error) if isinstance(error,BootError) and REASON.fullmatch(str(error)) else 'root_setup_failed'
        diag.event(diag.last_stage,'unknown' if reason.endswith('_unknown') else 'fail',reason)
        raise BootError(reason) from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','root','userspace'])
    args=parser.parse_args()
    try:
        if args.action=='prepare':
            prepare()
        elif args.action=='root':
            root()
        else:
            diagnostics()[0].event('userspace_ready')
    except (BootError,OSError,ValueError,subprocess.SubprocessError):
        print('sd-netboot failed; inspect diagnostics/boot-status.txt',flush=True)
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
