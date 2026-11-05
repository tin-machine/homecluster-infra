"""Bounded snapshot decoding and transactional cursors for fixed NAS log files."""
import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone

from nas_log_normal_patterns import RULE_VERSION, normal_pattern

MAX_FILE = 2 * 1024 * 1024
MAX_SNAPSHOT = 24 * 1024 * 1024 + 16384
MAX_STATE = 64 * 1024 * 1024
SOURCES = {
    'kernel': ('/mnt/HDA_ROOT/.logs/kmsg', ('current', '1', '2')),
    'samba': ('/usr/local/samba/var/log.samba', ('current', '1', '2')),
    'smbd': ('/usr/local/samba/var/log.smbd', ('current', 'old')),
    'nmbd': ('/var/log/log.nmbd', ('current', 'old')),
    'winbindd': ('/usr/local/samba/var/log.winbindd-idmap', ('current', 'old')),
    'storage': ('/var/log/storage_lib.log', ('current', 'old')),
    'hal': ('/var/log/hal_lib.log', ('current', 'old')),
    'hal_daemon': ('/var/log/hal_daemon.log', ('current', 'old')),
    'sdmd': ('/var/log/SDMD.log', ('current', 'old')),
    'storage_util': ('/var/log/storage_util.log', ('current',)),
    'iscsi': ('/var/log/iscsi_util.log', ('current',)),
}
SLOTS = [(kind, gen) for kind, (_, gens) in SOURCES.items() for gen in gens]
SAMBA_KINDS = {'samba', 'smbd', 'nmbd', 'winbindd'}
SAMBA_HEADER = re.compile(rb'^\[\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}')
HAL_DATE = re.compile(rb'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +[0-9]+ [0-9:]+ [0-9]{4}\s*$')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_snapshot(data):
    if not isinstance(data, bytes) or len(data) > MAX_SNAPSHOT or not data.startswith(b'NASLOG1\n'):
        raise ValueError('invalid snapshot')
    pos, records = 8, {}
    for kind, gen in SLOTS:
        end = data.find(b'\n', pos, pos + 256)
        if end < 0:
            raise ValueError('truncated header')
        fields = data[pos:end].split(b' ')
        pos = end + 1
        if fields == [b'M', kind.encode(), gen.encode()]:
            records[(kind, gen)] = None
            if gen == 'current':
                raise ValueError('current file missing')
            continue
        if (len(fields) != 7 or fields[:3] != [b'F', kind.encode(), gen.encode()]
                or any(not re.fullmatch(rb'[0-9]{1,18}', x) for x in fields[3:])):
            raise ValueError('invalid file header')
        dev, inode, size, mtime = map(int, fields[3:])
        if size > MAX_FILE or pos + size > len(data):
            raise ValueError('invalid file length')
        records[(kind, gen)] = {'dev': dev, 'inode': inode, 'mtime': mtime, 'data': data[pos:pos + size]}
        pos += size
    if data[pos:] != b'END\n':
        raise ValueError('incomplete or trailing snapshot')
    return records


def initial_state():
    return {'version': 1, 'cursors': {}, 'last_ns': 0, 'last_success': 0,
            'sent': 0, 'gaps': 0, 'normal': {}, 'pending': None}


def validate_state(state):
    if not isinstance(state, dict) or set(state) != set(initial_state()) or state['version'] != 1:
        raise ValueError('invalid state schema')
    for key in ('last_ns', 'last_success', 'sent', 'gaps'):
        if type(state[key]) not in (int, float) or not math.isfinite(state[key]) or state[key] < 0:
            raise ValueError('invalid state number')
    if not isinstance(state['cursors'], dict) or set(state['cursors']) - set(SOURCES):
        raise ValueError('invalid cursors')
    for cursor in state['cursors'].values():
        if not isinstance(cursor, dict) or set(cursor) != {'dev', 'inode', 'offset', 'sha256', 'size_seen'}:
            raise ValueError('invalid cursor')
        if any(type(cursor[k]) is not int or cursor[k] < 0 for k in ('dev', 'inode', 'offset', 'size_seen')):
            raise ValueError('invalid cursor number')
        if cursor['offset'] > MAX_FILE or cursor['size_seen'] > MAX_FILE or not re.fullmatch('[a-f0-9]{64}', cursor['sha256']):
            raise ValueError('invalid cursor bounds')
    if (not isinstance(state['normal'], dict) or len(state['normal']) > 256
            or any(not re.fullmatch(r'[a-z_]+/[a-z_]+', k) or type(v) is not int or v < 0 for k, v in state['normal'].items())):
        raise ValueError('invalid normal counters')
    pending = state['pending']
    if pending is not None:
        if not isinstance(pending, dict) or set(pending) != {'batches', 'next'} or not isinstance(pending['batches'], list) or len(pending['batches']) > 512:
            raise ValueError('invalid outbox')
        if pending['next'].get('pending') is not None:
            raise ValueError('nested pending state')
        validate_state(pending['next'])
        for batch in pending['batches']:
            if not isinstance(batch, dict) or set(batch) != {'streams'} or len(json.dumps(batch).encode()) > 262144:
                raise ValueError('invalid pending batch')
            for stream in batch['streams']:
                labels = stream['stream']
                if (labels.get('source') != 'qnap-filelog' or labels.get('environment') != 'production'
                        or labels.get('log_kind') not in SOURCES):
                    raise ValueError('invalid pending stream')
                for timestamp, line in stream['values']:
                    if not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdecimal() or not isinstance(line, str):
                        raise ValueError('invalid pending entry')
    return state


def completed_records(data, offset, kind, seen_size=-1, rotated=False):
    """Return raw records and byte offsets; retain an incomplete last line."""
    lines, pos = [], offset
    for part in data[offset:].split(b'\n')[:-1]:
        raw = part + b'\n'
        pos += len(raw)
        lines.append((raw, pos))
    if rotated and pos < len(data):
        lines.append((data[pos:], len(data)))
    groups = []
    for raw, end in lines:
        header = SAMBA_HEADER.match(raw) if kind in SAMBA_KINDS else HAL_DATE.match(raw) if kind == 'hal_daemon' else None
        if kind in SAMBA_KINDS or kind == 'hal_daemon':
            if header or not groups:
                groups.append([raw, end, bool(header), 1])
            else:
                groups[-1][0] += raw
                groups[-1][1] = end
                groups[-1][3] += 1
        else:
            groups.append([raw, end, False, 1])
    # Header/body writes can straddle a snapshot; wait one quiet poll for a lone header.
    if groups and groups[-1][2] and groups[-1][3] == 1 and not rotated and seen_size != len(data):
        groups.pop()
    return [(raw, end) for raw, end, _, _ in groups]


def matches_prefix(record, cursor):
    return (record is not None and len(record['data']) >= cursor['offset']
            and digest(record['data'][:cursor['offset']]) == cursor['sha256'])


def severity(kind, message):
    if kind == 'kernel':
        match = re.match(r'<([0-7])>', message)
        if match:
            return ('emerg', 'alert', 'critical', 'error', 'warn', 'info', 'info', 'debug')[int(match[1])]
    if re.search(r'(?i)\b(failed|failure|error|unable|not clean|not shut down properly|not compatible)\b', message):
        return 'error'
    if kind == 'hal' and 'Command_Timeout' in message and not re.search(r'raw = 0\s*$', message):
        return 'warn'
    return 'unknown'


def entry(kind, raw, bootstrap, now_ns):
    message = raw.decode('utf-8', 'replace').rstrip('\r\n')
    original = re.search(r'\d{4}[-/]\d{2}[-/]\d{2} \d{2}:\d{2}:\d{2}(?:\.[0-9]+)?', message)
    obj = {'message': message, 'source_file': SOURCES[kind][0], 'bootstrap': bootstrap,
           'observed_at': datetime.fromtimestamp(now_ns / 1e9, timezone.utc).isoformat(),
           'source_time': original[0] + ' JST' if original else None, 'filter_version': RULE_VERSION}
    level = severity(kind, message)
    while len(json.dumps(obj, ensure_ascii=False).encode()) > 16384:
        obj['message'] = obj['message'][:max(1, len(obj['message']) // 2)]
        obj['truncated'] = True
    return level, json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def prepare(state, snapshot, now_ns, filter_normal=True):
    """Create a durable outbox; acknowledge cursors only after every push succeeds."""
    validate_state(state)
    if state['pending'] is not None:
        return state
    nxt = copy.deepcopy(state)
    batches, batch, batch_size = [], {'streams': []}, 32
    for kind, (_, gens) in SOURCES.items():
        current = snapshot[(kind, 'current')]
        previous = state['cursors'].get(kind)
        chronological = [(gen, snapshot[(kind, gen)]) for gen in reversed(gens) if snapshot[(kind, gen)] is not None]
        if previous is None:
            work = [('current', current, 0)]
        else:
            found = next((i for i, (_, rec) in enumerate(chronological)
                          if (rec['dev'], rec['inode']) == (previous['dev'], previous['inode']) and matches_prefix(rec, previous)), None)
            if found is None:
                # QTS copytruncate gives the saved generation a different inode.
                found = next((i for i, (_, rec) in enumerate(chronological) if matches_prefix(rec, previous)), None)
            if found is None:
                nxt['gaps'] += 1
                work = [('current', current, 0)]
            else:
                work = [(gen, rec, previous['offset'] if i == found else 0)
                        for i, (gen, rec) in enumerate(chronological) if i >= found]
        for gen, rec, offset in work:
            records = completed_records(rec['data'], offset, kind,
                                        previous['size_seen'] if previous else -1, gen != 'current')
            end = records[-1][1] if records else offset
            if previous is None:
                records = records[-50:]
            for raw, _ in records:
                text = raw.decode('utf-8', 'replace').rstrip('\r\n')
                # Native severity is authoritative; words inside SMART attribute names are not.
                level = severity(kind, text) if kind == 'kernel' else 'unknown'
                normal = normal_pattern(kind, text, level) if filter_normal else None
                if normal:
                    key = kind + '/' + normal
                    nxt['normal'][key] = nxt['normal'].get(key, 0) + 1
                    continue
                nxt['last_ns'] = max(now_ns, int(nxt['last_ns']) + 1)
                level, line = entry(kind, raw, previous is None, nxt['last_ns'])
                stream = {'stream': {'environment': 'production', 'source': 'qnap-filelog',
                                     'service_name': 'qnap-nas', 'log_kind': kind, 'level': level},
                          'values': [[str(nxt['last_ns']), line]]}
                size = len(json.dumps(stream).encode()) + 2
                if batch_size + size > 128 * 1024:
                    batches.append(batch)
                    batch, batch_size = {'streams': []}, 32
                batch['streams'].append(stream)
                batch_size += size
                nxt['sent'] += 1
            if gen == 'current':
                nxt['cursors'][kind] = {'dev': rec['dev'], 'inode': rec['inode'], 'offset': end,
                                        'sha256': digest(rec['data'][:end]), 'size_seen': len(rec['data'])}
    if batch['streams']:
        batches.append(batch)
    if len(batches) > 512:
        raise ValueError('outbox limit exceeded')
    nxt['last_success'] = now_ns / 1e9
    out = copy.deepcopy(state)
    out['pending'] = {'batches': batches, 'next': nxt}
    validate_state(out)
    if len(json.dumps(out).encode()) > MAX_STATE:
        raise ValueError('state limit exceeded')
    return out
