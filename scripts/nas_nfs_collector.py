"""Bounded read-only SSH collector for NAS NFSv4 requests."""
import argparse
import ipaddress
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POLL_INTERVAL = 300


def parse_nfsd(text):
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError('invalid statistics')
    rows = [line.split() for line in text.splitlines() if line.split()[:1] == ['proc4']]
    if len(rows) != 1:
        raise ValueError('missing or duplicate proc4')
    row = rows[0]
    if len(row) != 4 or row[1] != '2' or any(not s.isascii() or not s.isdecimal() for s in row[1:]):
        raise ValueError('invalid proc4')
    return int(row[3])


def ssh_args(host):
    ip = ipaddress.IPv4Address(host)
    if str(ip) != host or not ip.is_private or any((ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified)):
        raise ValueError('invalid NAS address')
    args = ['ssh', '-F', '/dev/null', '-T']
    for option in ['BatchMode=yes', 'StrictHostKeyChecking=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none',
                   'ConnectTimeout=10', 'ConnectionAttempts=1', 'UserKnownHostsFile=/etc/nas-ssh/known_hosts',
                   'GlobalKnownHostsFile=/dev/null']:
        args += ['-o', option]
    return args + ['-i', '/etc/nas-ssh/id_rsa', 'admin@' + host, 'cat /proc/net/rpc/nfsd']


class Collector:
    def __init__(self, host, runner=subprocess.run, clock=time.time, monotonic=time.monotonic):
        self.args = ssh_args(host)
        self.runner, self.clock, self.monotonic = runner, clock, monotonic
        self.lock = threading.Lock()
        self.count, self.last, self.success, self.duration = None, 0, 0, 0

    def poll_once(self):
        start = self.monotonic()
        try:
            count = parse_nfsd(self.runner(self.args, capture_output=True, text=True, timeout=20, check=True).stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            count = None
        duration = max(0, self.monotonic() - start)
        with self.lock:
            self.duration, self.success = duration, int(count is not None)
            if count is not None:
                self.count, self.last = count, self.clock()

    def metrics(self):
        with self.lock:
            values = [('nas_nfs_collection_success', self.success, 'gauge'),
                      ('nas_nfs_last_success_timestamp_seconds', self.last, 'gauge'),
                      ('nas_nfs_collection_duration_seconds', self.duration, 'gauge')]
            if self.count is not None:
                values.append(('nas_nfs_v4_compound_total', self.count, 'counter'))
        return ''.join(f'# TYPE {name} {kind}\n{name} {value}\n' for name, value, kind in values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    collector = Collector(parser.parse_args().host)
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

    server = ThreadingHTTPServer(('0.0.0.0', 9189), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    thread.start()
    deadline = time.monotonic()
    try:
        while not stop.is_set():
            collector.poll_once()
            deadline += POLL_INTERVAL
            if deadline < time.monotonic():
                deadline = time.monotonic() + POLL_INTERVAL
            stop.wait(max(0, deadline - time.monotonic()))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
