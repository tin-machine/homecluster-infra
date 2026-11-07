"""Two-hour SMART snapshots with durable, disk-specific positive increments."""
import copy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time

POLL_INTERVAL = 7200
MAX_BYTES = 65536
FAULT_ATTRIBUTES = (5, 10, 184, 187, 188, 191, 197, 198, 199)
REQUIRED = set(FAULT_ATTRIBUTES) | {194}
HEX64 = re.compile(r'[0-9a-f]{64}')
ROW = re.compile(r'([0-9]{3})\s+[A-Za-z0-9_-]+\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([A-Za-z_-]+)')


def parse_snapshot(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError('invalid SMART snapshot size')
    lines = data.decode('ascii').splitlines()
    if not lines or lines.pop(0) != 'NAS_SMART_1' or not lines or lines.pop() != 'END':
        raise ValueError('incomplete SMART snapshot')
    disks = {}
    while lines:
        header = lines.pop(0).split()
        if (len(header) != 3 or header[0] != 'BAY' or header[1] not in ('1', '2', '3', '4')
                or header[1] in disks or not HEX64.fullmatch(header[2])):
            raise ValueError('invalid SMART bay')
        attrs, healthy = {}, True
        while lines and lines[0] != 'END_BAY':
            row = ROW.fullmatch(lines.pop(0).strip())
            if not row:
                raise ValueError('invalid SMART attribute')
            attribute, raw, value, worst, threshold = (int(x) for x in row.groups()[:5])
            if (not 1 <= attribute <= 255 or attribute in attrs or raw > 2**53 - 1
                    or any(x > 255 for x in (value, worst, threshold))):
                raise ValueError('invalid SMART numeric value')
            attrs[attribute] = raw
            healthy = healthy and row[6] == 'OK' and (threshold == 0 or value > threshold)
        if not lines or lines.pop(0) != 'END_BAY' or not REQUIRED <= attrs.keys():
            raise ValueError('missing SMART attributes')
        if not 0 <= attrs[194] <= 150:
            raise ValueError('invalid disk temperature')
        disks[header[1]] = {'disk_id': header[2], 'healthy': int(healthy),
                            'temperature': attrs[194],
                            'raw': {str(a): attrs[a] for a in FAULT_ATTRIBUTES}}
    if set(disks) != {'1', '2', '3', '4'} or len({d['disk_id'] for d in disks.values()}) != 4:
        raise ValueError('missing or duplicate disk identity')
    return disks


def integer(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def validate_state(state):
    if (not isinstance(state, dict) or set(state) != {'version', 'last_success', 'disks'}
            or type(state['version']) is not int or state['version'] != 1 or type(state['last_success']) not in (int, float)
            or not math.isfinite(state['last_success']) or state['last_success'] < 0):
        raise ValueError('invalid SMART state')
    disks = state['disks']
    if not isinstance(disks, dict) or set(disks) != {'1', '2', '3', '4'}:
        raise ValueError('invalid SMART state bays')
    for disk in disks.values():
        if (not isinstance(disk, dict) or set(disk) != {'disk_id', 'healthy', 'temperature', 'raw', 'increments'}
                or not isinstance(disk['disk_id'], str) or not HEX64.fullmatch(disk['disk_id'])
                or type(disk['healthy']) is not int or disk['healthy'] not in (0, 1)
                or not integer(disk['temperature']) or disk['temperature'] > 150):
            raise ValueError('invalid SMART state disk')
        for key in ('raw', 'increments'):
            if (not isinstance(disk[key], dict) or set(disk[key]) != {str(a) for a in FAULT_ATTRIBUTES}
                    or not all(integer(v) for v in disk[key].values())):
                raise ValueError('invalid SMART state attributes')
    if len({d['disk_id'] for d in disks.values()}) != 4:
        raise ValueError('duplicate SMART state identity')
    return state


def load_state(path):
    if path.is_symlink():
        raise ValueError('SMART state is a symlink')
    try:
        with path.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(data) > MAX_BYTES:
        raise ValueError('SMART state too large')
    return validate_state(json.loads(data))


def save_state(path, state):
    data = json.dumps(validate_state(state), allow_nan=False, separators=(',', ':')).encode()
    if len(data) > MAX_BYTES or path.is_symlink():
        raise ValueError('invalid SMART state destination')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.smart-', delete=False) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary:
            os.unlink(temporary)


def advance(previous, disks, now):
    disks = copy.deepcopy(disks)
    for bay, disk in disks.items():
        old = previous['disks'][bay] if previous else None
        same = old is not None and old['disk_id'] == disk['disk_id']
        disk['increments'] = {a: old['increments'][a] + max(0, raw - old['raw'][a]) if same else 0
                              for a, raw in disk['raw'].items()}
    return validate_state({'version': 1, 'last_success': now, 'disks': disks})


def fetch(args):
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(20, process.kill)
    timer.daemon = True
    timer.start()
    try:
        data = process.stdout.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('SMART snapshot exceeds limit')
        if process.wait(timeout=2) != 0:
            raise ValueError('SMART SSH failed')
        return parse_snapshot(data)
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


class Collector:
    def __init__(self, log_ssh_args, path, reader=fetch, saver=save_state, clock=time.time):
        self.args = [*log_ssh_args[:-1], 'nas-smart-v1']
        self.path, self.reader, self.saver, self.clock = Path(path), reader, saver, clock
        self.lock = threading.Lock()
        self.state = None
        self.success = self.state_valid = 0

    def poll_once(self):
        try:
            # Re-read persisted state even after a failed fsync/replace. Never double count a retry.
            previous = load_state(self.path)
        except Exception:
            with self.lock:
                self.success = self.state_valid = 0
            return
        with self.lock:
            self.state, self.state_valid = previous, 1
        try:
            candidate = advance(previous, self.reader(self.args), self.clock())
        except Exception:
            with self.lock:
                self.success = 0
            return
        try:
            self.saver(self.path, candidate)
        except Exception:
            with self.lock:
                self.success = self.state_valid = 0
            return
        with self.lock:
            self.state, self.success, self.state_valid = candidate, 1, 1

    def run(self, stop):
        while not stop.is_set():
            self.poll_once()
            # Two hours after this bounded collection; never catch up with a burst of reads.
            stop.wait(POLL_INTERVAL)

    def metrics(self):
        with self.lock:
            state = self.state
            result = (f'nas_smart_collection_success {self.success}\n'
                      f'nas_smart_state_valid {self.state_valid}\n'
                      f'nas_smart_last_success_timestamp_seconds {state["last_success"] if state else 0}\n')
            if not self.success or state is None:
                return result
            result += '# TYPE nas_smart_attribute_increments_total counter\n'
            for bay, disk in sorted(state['disks'].items()):
                labels = f'bay="{bay}",disk_id="{disk["disk_id"]}"'
                result += f'nas_smart_disk_healthy{{{labels}}} {disk["healthy"]}\n'
                result += f'nas_smart_temperature_celsius{{{labels}}} {disk["temperature"]}\n'
                for attr in disk['raw']:
                    tag = labels + f',attribute_id="{attr}"'
                    result += f'nas_smart_attribute_raw{{{tag}}} {disk["raw"][attr]}\n'
                    result += f'nas_smart_attribute_increments_total{{{tag}}} {disk["increments"][attr]}\n'
            return result
