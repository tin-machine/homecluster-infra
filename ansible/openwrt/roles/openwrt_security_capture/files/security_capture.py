#!/usr/bin/env python3
"""Bounded, fixed-profile capture. Requests carry no commands or BPF text."""
from __future__ import annotations
import argparse
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import time
import uuid

CONFIG = Path('/etc/homecluster-security-capture.json')
STATE = Path('/var/run/homecluster-security-capture')
SPOOL = Path('/srv/security-capture')
LIMIT = 24 + 1000 * (16 + 65535)
ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z')
NAME = re.compile(r'[a-z0-9][a-z0-9_.-]{0,63}\Z')


class CaptureError(ValueError):
    """Fixed public reason code, never argv, BPF or packet data."""


def reason(exc):
    return str(exc) if isinstance(exc, CaptureError) else type(exc).__name__


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(path):
    with path.open('rb') as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def read_private(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 16384:
        raise CaptureError('private_file_contract')
    return json.loads(path.read_text())


def directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise CaptureError('private_directory_contract')


def publish(path, data):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, separators=(',', ':')); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def config(path=CONFIG):
    data = read_private(path)
    if set(data) != {'schema', 'profiles'} or type(data['schema']) is not int or data['schema'] != 1 or not isinstance(data['profiles'], dict):
        raise CaptureError('configuration_schema')
    if not 1 <= len(data['profiles']) <= 8:
        raise CaptureError('profile_count')
    for name, profile in data['profiles'].items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(profile, dict):
            raise CaptureError('profile_name')
        if set(profile) != {'interface', 'filter', 'duration_seconds'}:
            raise CaptureError('profile_schema')
        interface, bpf, duration = (profile[k] for k in ['interface', 'filter', 'duration_seconds'])
        if not isinstance(interface, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,31}', interface):
            raise CaptureError('interface')
        if not isinstance(bpf, str) or not 1 <= len(bpf) <= 4096 or '\x00' in bpf or '\n' in bpf:
            raise CaptureError('filter')
        if type(duration) is not int or not 1 <= duration <= 180:
            raise CaptureError('duration')
    return data


def pcap(path):
    """Validate every closed classic PCAP record; no packet content is emitted."""
    size = path.stat().st_size
    if size < 24 or size > LIMIT:
        raise CaptureError('pcap_size')
    count = truncated = 0
    with path.open('rb') as f:
        head = f.read(24)
        endian = {b'\xd4\xc3\xb2\xa1': '<', b'\xa1\xb2\xc3\xd4': '>',
                  b'\x4d\x3c\xb2\xa1': '<', b'\xa1\xb2\x3c\x4d': '>'}.get(head[:4])
        if endian is None:
            raise CaptureError('pcap_magic')
        major, minor, _, _, snaplen, linktype = struct.unpack(endian+'HHiIII', head[4:])
        if (major, minor) != (2, 4) or snaplen != 65535:
            raise CaptureError('pcap_header')
        while header := f.read(16):
            if len(header) != 16:
                raise CaptureError('pcap_record_header')
            _, _, included, original = struct.unpack(endian+'IIII', header)
            if included > snaplen or included > original or len(f.read(included)) != included:
                raise CaptureError('pcap_record_length')
            count += 1; truncated += included < original
            if count > 1000:
                raise CaptureError('pcap_packet_limit')
    return {'size_bytes': size, 'packet_count': count, 'truncated_packet_count': truncated,
            'snaplen': snaplen, 'linktype': linktype, 'pcap_sha256': digest(path)}


def guards(spool=SPOOL):
    if not os.path.ismount('/srv') or Path('/srv').is_symlink():
        raise CaptureError('srv_not_mounted')
    directory(spool)
    entries = list(spool.iterdir())
    if any(x.is_symlink() or not x.is_dir() for x in entries):
        raise CaptureError('unexpected_spool_entry')
    # Ready, failed and interrupted runs all consume the same bounded budget.
    if len(entries) >= 2:
        raise CaptureError('spool_count_limit')
    if any(time.time() - x.stat().st_mtime > 86400 for x in entries):
        raise CaptureError('spool_expired_requires_review')
    fs = os.statvfs(spool)
    if fs.f_bavail * fs.f_frsize < 8 * 1024**3 + LIMIT + 256 * 1024**2:
        raise CaptureError('shared_storage_reserve')
    memory = {x.split(':', 1)[0]: x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines()}
    if int(memory['MemAvailable']) * 1024 < 256 * 1024**2:
        raise CaptureError('router_memory')
    if os.getloadavg()[0] > 2:
        raise CaptureError('router_load')


def lock():
    directory(STATE)
    fd = os.open(STATE/'capture.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd); raise
    return os.fdopen(fd, 'r+')


def offload_state():
    result = {'software_config': None, 'hardware_config': None, 'nft_flowtable_count': None}
    for key, option in [('software_config', 'flow_offloading'), ('hardware_config', 'flow_offloading_hw')]:
        try:
            r = subprocess.run(['/sbin/uci', '-q', 'get', 'firewall.@defaults[0].'+option],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0 and r.stdout.strip() in ['0', '1']:
                result[key] = r.stdout.strip() == '1'
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        r = subprocess.run(['/usr/sbin/nft', '-j', 'list', 'flowtables'],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            entries = json.loads(r.stdout)['nftables']
            if isinstance(entries, list):
                result['nft_flowtable_count'] = sum(isinstance(x, dict) and 'flowtable' in x for x in entries)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        pass
    return result


def child_contract(parent):
    # Kernel stops only this child if its supervisor crashes or is SIGKILLed.
    # Check the parent again to close the fork -> prctl race.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGINT, 0, 0, 0) != 0:
        os._exit(125)
    if os.getppid() != parent:
        os._exit(125)


def run_tcpdump(argv, duration):
    parent = os.getpid()
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, preexec_fn=lambda: child_contract(parent))
    interrupted = False

    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
        process.send_signal(signal.SIGINT)
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        try:
            _, error = process.communicate(timeout=duration)
            reason = 'interrupted' if interrupted else 'packet_limit'
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGINT)
            try:
                _, error = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate()
                raise CaptureError('tcpdump_flush_timeout')
            reason = 'interrupted' if interrupted else 'duration_limit'
        if interrupted or process.returncode not in [0, -signal.SIGINT]:
            raise CaptureError('tcpdump_failed_or_interrupted')
        found = re.search(r'(\d+) packets dropped by kernel', error)
        return reason, int(found[1]) if found else None
    finally:
        signal.signal(signal.SIGTERM, previous)
        if process.poll() is None:
            process.kill(); process.communicate()


def request(profile):
    data = config()
    if profile not in data['profiles']:
        raise CaptureError('profile_not_allowed')
    with lock():
        if (STATE/'request.json').exists():
            raise CaptureError('request_pending_requires_review')
        guards()
        capture_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')+uuid.uuid4().hex[:16]
        publish(STATE/'request.json', {'schema': 1, 'capture_id': capture_id, 'profile': profile,
                                    'created_epoch': time.time(), 'configuration_sha256': digest(CONFIG)})
        publish(STATE/'last-result.json', {'status': 'pending', 'capture_id': capture_id})
    result = subprocess.run(['/etc/init.d/homecluster-security-capture', 'start'],
                            capture_output=True, timeout=15)
    if result.returncode:
        raise CaptureError('procd_start_failed_request_preserved')
    print(json.dumps({'status': 'accepted', 'capture_id': capture_id}))


def serve():
    with lock():
        item = read_private(STATE/'request.json')
        if set(item) != {'schema', 'capture_id', 'profile', 'created_epoch', 'configuration_sha256'} or type(item['schema']) is not int or item['schema'] != 1:
            raise CaptureError('request_schema')
        if not isinstance(item['capture_id'], str) or not ID.fullmatch(item['capture_id']):
            raise CaptureError('capture_id')
        if type(item['created_epoch']) not in [int, float] or not 0 <= time.time() - item['created_epoch'] <= 120:
            raise CaptureError('request_expired')
        data = config()
        if item['configuration_sha256'] != digest(CONFIG) or item['profile'] not in data['profiles']:
            raise CaptureError('configuration_changed')
        guards()
        run = SPOOL/item['capture_id']; run.mkdir(mode=0o700, exist_ok=False)
        publish(run/'started.json', item)
        publish(STATE/'last-result.json', {'status': 'running', 'capture_id': item['capture_id']})
        (STATE/'request.json').unlink()
        profile = data['profiles'][item['profile']]
        start = utc()
        try:
            filter_file = run/'filter.bpf'
            filter_file.write_text(profile['filter']+'\n'); filter_file.chmod(0o600)
            compile_result = subprocess.run(['/usr/bin/tcpdump', '-i', profile['interface'], '-ddd', '-F', str(filter_file)],
                                            capture_output=True, timeout=10)
            if compile_result.returncode:
                raise CaptureError('filter_compile_failed')
            offload = offload_state()
            part = run/'capture.pcap.part'
            argv = ['/usr/bin/tcpdump', '-i', profile['interface'], '-nn', '-p', '-s', '65535', '-c', '1000',
                    '-U', '-Z', 'root', '-w', str(part), '-F', str(filter_file)]
            stop_reason, drops = run_tcpdump(argv, profile['duration_seconds'])
            summary = pcap(part)
            if stop_reason == 'packet_limit' and summary['packet_count'] != 1000:
                raise CaptureError('unexpected_early_exit')
            part.chmod(0o600)
            with part.open('rb') as f:
                os.fsync(f.fileno())
            part.rename(run/'capture.pcap')
            summary.update(schema=1, capture_id=item['capture_id'], profile=item['profile'], interface=profile['interface'],
                           started_at=start, ended_at=utc(), stop_reason=stop_reason, kernel_drop_count=drops,
                           configuration_sha256=item['configuration_sha256'], offload_state=offload, offload_visibility='unknown')
            publish(run/'manifest.json', summary)  # Completion marker is last.
            publish(STATE/'last-result.json', {'status': 'complete', 'capture_id': item['capture_id'], 'stop_reason': stop_reason})
        except Exception as exc:
            # No exception message/argv/filter content leaves this private run.
            publish(run/'failed.json', {'status': 'failed', 'capture_id': item['capture_id'], 'reason': reason(exc), 'ended_at': utc()})
            publish(STATE/'last-result.json', {'status': 'failed', 'capture_id': item['capture_id']})
            raise


def status():
    path = STATE/'last-result.json'
    if not path.exists():
        return {'status': 'unknown'}
    result = read_private(path)
    if result.get('status') not in ['pending', 'running', 'complete', 'failed']:
        return {'status': 'unknown'}
    if result['status'] == 'running':
        fd = os.open(STATE/'capture.lock', os.O_RDONLY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return result
        finally:
            os.close(fd)
        return {'status': 'unknown', 'capture_id': result.get('capture_id'), 'reason': 'supervisor_not_active'}
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['request', 'serve', 'status', 'validate'])
    parser.add_argument('profile', nargs='?')
    args = parser.parse_args()
    if args.action == 'validate' and args.profile:
        config(Path(args.profile)); print(json.dumps({'status': 'valid'}))
    elif args.action == 'request' and args.profile:
        request(args.profile)
    elif args.action == 'serve' and not args.profile:
        serve()
    elif args.action == 'status' and not args.profile:
        print(json.dumps(status()))
    else:
        parser.error('request requires one fixed profile; other actions accept no profile')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'reason': reason(exc)}))
        raise SystemExit(1)
