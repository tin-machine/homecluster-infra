"""Read-only NAS snapshots -> durable, at-least-once Loki outbox."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from nas_file_logs_core import MAX_SNAPSHOT, MAX_STATE, initial_state, parse_snapshot, prepare, validate_state

LOKI_URL = 'http://prod-loki:3100/loki/api/v1/push'


def ssh_args(host):
    address = ipaddress.IPv4Address(host)
    if str(address) != host or not address.is_private or any((address.is_loopback, address.is_link_local, address.is_multicast, address.is_reserved, address.is_unspecified)):
        raise ValueError('invalid NAS address')
    args = ['ssh', '-F', '/dev/null', '-T']
    for option in ['BatchMode=yes', 'StrictHostKeyChecking=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none',
                   'ConnectTimeout=10', 'ConnectionAttempts=1', 'UserKnownHostsFile=/etc/nas-ssh/known_hosts',
                   'GlobalKnownHostsFile=/dev/null', 'ServerAliveInterval=5', 'ServerAliveCountMax=2']:
        args += ['-o', option]
    return args + ['-i', '/etc/nas-ssh/id_rsa', 'admin@' + host, 'nas-logs-v1']


def atomic_save(path, state):
    validate_state(state)
    data = json.dumps(state, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > MAX_STATE:
        raise ValueError('state too large')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.nas-state-', delete=False) as file:
            temporary = file.name
            os.fchmod(file.fileno(), 0o600)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
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


def load_state(path):
    if path.is_symlink():
        raise ValueError('state must not be a symlink')
    try:
        with path.open('rb') as file:
            data = file.read(MAX_STATE + 1)
    except FileNotFoundError:
        return initial_state()
    if len(data) > MAX_STATE:
        raise ValueError('state too large')
    return validate_state(json.loads(data))


def fetch_snapshot(args):
    # The forced server command caps the aggregate payload; cap the client read too.
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(20, process.kill)
    timer.daemon = True
    timer.start()
    try:
        data = process.stdout.read(MAX_SNAPSHOT + 1)
        if len(data) > MAX_SNAPSHOT:
            process.kill()
            raise ValueError('snapshot exceeds limit')
        if process.wait(timeout=2) != 0:
            raise ValueError('SSH snapshot failed')
        return parse_snapshot(data)
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def push(batch):
    body = json.dumps(batch, ensure_ascii=False, separators=(',', ':')).encode()
    if len(body) > 256 * 1024:
        raise ValueError('push exceeds limit')
    request = Request(LOKI_URL, data=body, headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=5) as response:
        if response.status != 204:
            raise ValueError('Loki rejected push')


class Collector:
    def __init__(self, host, state_path, filter_normal=True, fetch=fetch_snapshot, sender=push, clock=time.time_ns):
        self.args = ssh_args(host)
        self.path = Path(state_path)
        self.filter_normal, self.fetch, self.sender, self.clock = filter_normal, fetch, sender, clock
        self.lock = threading.Lock()
        self.state = None
        self.success = self.state_valid = self.push_failures = self.collection_failures = 0

    def poll_once(self):
        try:
            if self.state is None:
                self.state = load_state(self.path)
            self.state_valid = 1
            if self.state['pending'] is None:
                candidate = prepare(self.state, self.fetch(self.args), self.clock(), self.filter_normal)
                atomic_save(self.path, candidate)
                with self.lock:
                    self.state = candidate
            pending = self.state['pending']
            deadline = time.monotonic() + 55
            for batch in pending['batches']:
                try:
                    if time.monotonic() > deadline:
                        raise TimeoutError('delivery budget exceeded')
                    self.sender(batch)
                except Exception:
                    self.push_failures += 1
                    raise
            committed = pending['next']
            atomic_save(self.path, committed)
            with self.lock:
                self.state, self.success = committed, 1
        except Exception:
            # Do not log raw records, paths from exceptions, or key material.
            self.success = 0
            self.collection_failures += 1
            if self.state is None:
                self.state_valid = 0

    def metrics(self):
        with self.lock:
            state = self.state or initial_state()
            values = {'collection_success': self.success, 'state_valid': self.state_valid,
                      'last_success_timestamp_seconds': state['last_success'],
                      'sent_entries_total': state['sent'], 'rotation_gaps_total': state['gaps'],
                      'push_failures_total': self.push_failures, 'collection_failures_total': self.collection_failures}
            result = ''.join(f'nas_file_logs_{name} {value}\n' for name, value in values.items())
            for key, count in sorted(state['normal'].items()):
                kind, pattern = key.split('/')
                result += f'nas_file_logs_filtered_total{{log_kind="{kind}",pattern="{pattern}"}} {count}\n'
            return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--state', default='/data/state.json')
    parser.add_argument('--filter-normal', choices=('true', 'false'), default='false')
    args = parser.parse_args()
    collector = Collector(args.host, args.state, args.filter_normal == 'true')
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = collector.metrics().encode() if self.path == '/metrics' else b'ok'
            self.send_response(200 if self.path in ('/metrics', '/healthz') else 404)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('0.0.0.0', 9188), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set():
            started = time.monotonic()
            collector.poll_once()
            stop.wait(max(1, 60 - (time.monotonic() - started)))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
