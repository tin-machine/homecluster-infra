#!/usr/bin/env python3
"""Authenticated fixed-profile protocol. The SSH key is a trusted authorizer.

No listener, automatic dispatch, shell, filter or path is accepted from requests.
An unsettled reservation survives ambiguous starts and reboots; never reset it on
transport failure. Planning and status do not release or refresh reservations.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import uuid

_source = Path(__file__).with_name('homecluster-security-capture.py')
if not _source.exists():
    _source = Path(__file__).with_name('security_capture.py')
_spec = importlib.util.spec_from_file_location('capture_mechanism', _source)
cap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cap)
CONFIG = Path('/etc/homecluster-security-trigger.json')
ROOT = Path('/srv/security-trigger')
HEX = re.compile(r'[a-f0-9]{64}\Z')
NONCE = re.compile(r'[a-f0-9]{32}\Z')
COMMAND = 'homecluster-security-trigger-v1'


def require(value, reason):
    if not value:
        raise cap.CaptureError(reason)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def epoch(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 1_700_000_000


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'trigger_duplicate_key')
            result[key] = value
        return result
    def constant(_):
        raise cap.CaptureError('trigger_nonfinite_json')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def read(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 16384, 'trigger_private_file')
    return strict_json(path.read_bytes())


def configuration(path=CONFIG):
    value = read(path)
    require(isinstance(value, dict) and set(value) == {'schema', 'automatic', 'profiles'} and
            type(value['schema']) is int and value['schema'] == 1 and value['automatic'] is False and
            isinstance(value['profiles'], dict) and 1 <= len(value['profiles']) <= 8, 'trigger_configuration')
    profiles = cap.config()['profiles']
    for name, target in value['profiles'].items():
        require(name in profiles and isinstance(target, str) and cap.NAME.fullmatch(target), 'trigger_profile_binding')
    return value


def load_state():
    path = ROOT / 'state.json'
    # Only Ansible initializes a new ledger. Loss/corruption is never a reset.
    value = read(path)
    require(isinstance(value, dict) and set(value) == {'schema', 'last_epoch', 'events', 'active', 'last_settled'} and
            type(value['schema']) is int and value['schema'] == 1 and epoch(value['last_epoch']) and
            isinstance(value['events'], list) and len(value['events']) <= 10, 'trigger_state_invalid')
    for event in value['events']:
        require(isinstance(event, dict) and set(event) == {'nonce', 'at', 'target'} and
                isinstance(event['nonce'], str) and NONCE.fullmatch(event['nonce']) and epoch(event['at']) and
                event['at'] <= value['last_epoch'] and isinstance(event['target'], str) and
                cap.NAME.fullmatch(event['target']), 'trigger_state_event_invalid')
    active = value['active']
    if active is not None:
        require(isinstance(active, dict) and set(active) == {'nonce', 'plan_id', 'capture_id', 'target'} and
                isinstance(active['nonce'], str) and NONCE.fullmatch(active['nonce']) and
                isinstance(active['plan_id'], str) and HEX.fullmatch(active['plan_id']) and
                (active['capture_id'] is None or isinstance(active['capture_id'], str) and cap.ID.fullmatch(active['capture_id'])) and
                isinstance(active['target'], str) and cap.NAME.fullmatch(active['target']) and
                any(row['nonce'] == active['nonce'] and row['target'] == active['target'] for row in value['events']),
                'trigger_state_active_invalid')
    settled = value['last_settled']
    if settled is not None:
        require(isinstance(settled, dict) and set(settled) == {'plan_id', 'capture_id', 'analysis_id'} and
                all(isinstance(settled[k], str) and HEX.fullmatch(settled[k]) for k in ('plan_id', 'analysis_id')) and
                isinstance(settled['capture_id'], str) and cap.ID.fullmatch(settled['capture_id']), 'trigger_settled_invalid')
    return value


@contextlib.contextmanager
def ledger():
    require(os.path.ismount('/srv') and not Path('/srv').is_symlink(), 'srv_not_mounted')
    cap.private_directory(ROOT)
    info = ROOT.lstat()
    require(stat.S_IMODE(info.st_mode) == 0o700, 'trigger_private_directory')
    fd = os.open(ROOT / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600, 'trigger_lock_invalid')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield load_state()
    finally:
        os.close(fd)


def plan_fields(request, config):
    return {'schema': 1, 'nonce': request['nonce'], 'created_at': request['created_at'],
            'profile': request['profile'], 'target': config['profiles'][request['profile']],
            'capture_configuration_sha256': cap.digest(cap.CONFIG), 'trigger_configuration_sha256': fingerprint(config)}


def start_guards(state, now, target):
    require(epoch(now) and now >= state['last_epoch'], 'trigger_clock_rollback')
    require(state['active'] is None, 'trigger_unsettled_reservation')
    events = [row for row in state['events'] if now - row['at'] < 86400]
    require(len(events) < 10, 'trigger_daily_limit')
    require(sum(now - row['at'] < 3600 for row in events) < 3, 'trigger_hourly_limit')
    require(not any(row['target'] == target and now - row['at'] < 1800 for row in events), 'trigger_target_cooldown')
    # Existing capture/CPU/memory/spool guard, without launching a service.
    with cap.lock():
        require(not (cap.STATE / 'request.json').exists(), 'request_pending_requires_review')
        cap.guards()
    return events


def dispatch(request, now=None):
    now = time.time() if now is None else now
    require(isinstance(request, dict) and request.get('action') in {'plan', 'start', 'status', 'settle'}, 'trigger_action')
    action = request['action']
    base = {'schema', 'action', 'nonce', 'created_at'}
    extra = {'plan': {'profile'}, 'start': {'profile', 'plan_id', 'guarded_at'},
             'status': set(), 'settle': {'plan_id', 'capture_id', 'analysis_id', 'guarded_at'}}[action]
    require(set(request) == base | extra and type(request['schema']) is int and request['schema'] == 1 and
            isinstance(request['nonce'], str) and NONCE.fullmatch(request['nonce']) and epoch(request['created_at']) and
            epoch(now) and 0 <= now - request['created_at'] <= 120, 'trigger_request_schema_or_time')
    config = configuration()
    with ledger() as state:
        require(now >= state['last_epoch'], 'trigger_clock_rollback')
        if action == 'status':
            return {'status': 'idle' if state['active'] is None else 'unsettled', 'active': state['active'],
                    'last_settled': state['last_settled'], 'capture': cap.status(), 'automatic': False}
        if action == 'settle':
            active = state['active']
            require(isinstance(active, dict) and request['plan_id'] == active['plan_id'] and
                    request['capture_id'] == active['capture_id'] and isinstance(request['analysis_id'], str) and
                    HEX.fullmatch(request['analysis_id']) and epoch(request['guarded_at']) and
                    0 <= now - request['guarded_at'] <= 30, 'trigger_settlement_mismatch')
            # The authorizer first checks NAS + terminal analysis + destination-bound delivery.
            # The router independently checks its durable source ACK and capture identity.
            ack = cap.read_private(cap.ACKROOT / (request['capture_id'] + '.json'))
            require(ack.get('capture_id') == request['capture_id'] and ack.get('status') == 'acknowledged',
                    'trigger_source_not_acknowledged')
            state.update(active=None, last_epoch=now,
                         last_settled={k: request[k] for k in ('plan_id', 'capture_id', 'analysis_id')})
            cap.publish(ROOT / 'state.json', state)
            return {'status': 'settled', 'plan_id': request['plan_id'], 'capture_id': request['capture_id']}
        require(isinstance(request['profile'], str) and request['profile'] in config['profiles'], 'trigger_profile_not_allowed')
        fields = plan_fields(request, config)
        plan_id = fingerprint(fields)
        require(not any(row['nonce'] == request['nonce'] for row in state['events']), 'trigger_replay')
        events = start_guards(state, now, fields['target'])
        if action == 'plan':
            return dict(status='planned', plan_id=plan_id, expires_at=request['created_at'] + 120, **fields)
        require(request['plan_id'] == plan_id and epoch(request['guarded_at']) and
                0 <= now - request['guarded_at'] <= 30, 'trigger_plan_or_observation_changed')
        state['events'] = events + [{'nonce': request['nonce'], 'at': now, 'target': fields['target']}]
        state['last_epoch'] = now
        cid = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:16]
        state['active'] = {'nonce': request['nonce'], 'plan_id': plan_id, 'capture_id': cid, 'target': fields['target']}
        cap.publish(ROOT / 'state.json', state)  # Reserve BEFORE any possibly ambiguous launch.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cap.request(request['profile'], expected_sha256=fields['capture_configuration_sha256'], planned_capture_id=cid)
        result = json.loads(output.getvalue())
        require(result.get('status') == 'accepted' and result.get('capture_id') == cid, 'trigger_start_unknown')
        state['active']['capture_id'] = result['capture_id']
        cap.publish(ROOT / 'state.json', state)
        return dict(status='accepted', plan_id=plan_id, capture_id=result['capture_id'], automatic=False)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('ssh', 'validate', 'initialize'))
    parser.add_argument('path', nargs='?', type=Path)
    args = parser.parse_args()
    if args.action == 'initialize' and args.path is None:
        require(os.path.ismount('/srv') and not CONFIG.exists() and epoch(time.time()), 'trigger_initialization_contract')
        ROOT.mkdir(mode=0o700, exist_ok=False)
        cap.publish(ROOT / 'state.json', {'schema': 1, 'last_epoch': time.time(), 'events': [], 'active': None, 'last_settled': None})
        print(json.dumps({'status': 'initialized'}))
        return
    if args.action == 'validate' and args.path:
        configuration(args.path)
        print(json.dumps({'status': 'valid'}))
        return
    require(args.action == 'ssh' and args.path is None and os.environ.get('SSH_ORIGINAL_COMMAND') == COMMAND,
            'trigger_forced_command')
    def expired(*_):
        raise cap.CaptureError('trigger_input_timeout')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(10)
    raw = sys.stdin.buffer.read(4097)
    require(len(raw) <= 4096, 'trigger_input_limit')
    signal.alarm(0)
    print(json.dumps(dispatch(strict_json(raw)), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'reason': cap.reason(error)}))
        sys.exit(1)
