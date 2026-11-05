import copy
import json
from pathlib import Path
import tempfile
import unittest

from nas_file_logs_collector import Collector, load_state, ssh_args
from nas_file_logs_core import SOURCES, SLOTS, completed_records, initial_state, parse_snapshot, prepare
from nas_log_normal_patterns import normal_pattern


def snapshot(changes=None):
    result = {}
    for i, (kind, gen) in enumerate(SLOTS):
        result[(kind, gen)] = {'dev': 1, 'inode': i + 1, 'mtime': 100, 'data': b''} if gen == 'current' else None
    for key, value in (changes or {}).items():
        if isinstance(value, bytes):
            result[key]['data'] = value
        else:
            result[key] = value
    return result


def encoded(records):
    parts = [b'NASLOG1\n']
    for kind, gen in SLOTS:
        rec = records[(kind, gen)]
        if rec is None:
            parts.append(f'M {kind} {gen}\n'.encode())
        else:
            parts += [f'F {kind} {gen} {rec["dev"]} {rec["inode"]} {len(rec["data"])} {rec["mtime"]}\n'.encode(), rec['data']]
    return b''.join(parts) + b'END\n'


def messages(state):
    return [json.loads(line)['message'] for batch in state['pending']['batches']
            for stream in batch['streams'] for _, line in stream['values']]


def transaction(state, records, now=1_000_000_000):
    return prepare(state, records, now, filter_normal=False)


class CoreTests(unittest.TestCase):
    def test_strict_protocol(self):
        recs = snapshot({('kernel', 'current'): b'a\x00b\n'})
        data = encoded(recs)
        self.assertEqual(parse_snapshot(data), recs)
        for bad in [data[:-1], data + b'garbage', data.replace(b'F kernel current 1 1 4 100', b'F kernel current 1 1 2097153 100'),
                    data.replace(b'F kernel current 1 1', b'F kernel current -1 1'),
                    data.replace(b'M kernel 1', b'M unknown 1'), data.replace(b'F kernel current 1 1 4 100\na\x00b\n', b'M kernel current\n')]:
            with self.assertRaises(ValueError):
                parse_snapshot(bad)

    def test_initial_tail_and_append_partial(self):
        records = snapshot({('kernel', 'current'): b''.join(f'{i}\n'.encode() for i in range(80)) + b'partial'})
        first = transaction(initial_state(), records)
        self.assertEqual(messages(first), [str(i) for i in range(30, 80)])
        committed = first['pending']['next']
        self.assertEqual(messages(transaction(committed, records)), [])
        records[('kernel', 'current')]['data'] += b' completed\nnew\n'
        self.assertEqual(messages(transaction(committed, records)), ['partial completed', 'new'])

    def test_rename_and_multiple_generations(self):
        base = snapshot({('kernel', 'current'): b'old\n'})
        state = transaction(initial_state(), base)['pending']['next']
        old = {**base[('kernel', 'current')], 'data': b'old\ntail\n'}
        records = snapshot({('kernel', '2'): old, ('kernel', '1'): {'dev': 1, 'inode': 80, 'mtime': 101, 'data': b'middle\n'},
                            ('kernel', 'current'): {'dev': 1, 'inode': 81, 'mtime': 102, 'data': b'current\n'}})
        pending = transaction(state, records)
        self.assertEqual(messages(pending), ['tail', 'middle', 'current'])
        self.assertEqual(pending['pending']['next']['gaps'], 0)

    def test_rotated_final_line_without_newline_is_preserved(self):
        base = snapshot({('kernel', 'current'): b'old\npartial warning'})
        state = transaction(initial_state(), base)['pending']['next']
        records = snapshot({('kernel', '1'): base[('kernel', 'current')],
                            ('kernel', 'current'): {'dev': 1, 'inode': 81, 'mtime': 102, 'data': b'current\n'}})
        self.assertEqual(messages(transaction(state, records)), ['partial warning', 'current'])

    def test_copytruncate_and_lost_rotation(self):
        base = snapshot({('kernel', 'current'): b'old-long\n'})
        state = transaction(initial_state(), base)['pending']['next']
        records = snapshot({('kernel', 'current'): b'new\n', ('kernel', '1'): {'dev': 1, 'inode': 99, 'mtime': 100, 'data': b'old-long\ntail\n'}})
        result = transaction(state, records)
        self.assertEqual(messages(result), ['tail', 'new'])
        self.assertEqual(result['pending']['next']['gaps'], 0)
        records[('kernel', '1')] = None
        result = transaction(state, records)
        self.assertEqual(messages(result), ['new'])
        self.assertEqual(result['pending']['next']['gaps'], 1)

    def test_multiline_and_split_header(self):
        header = b'[2026/11/05 12:00:00.000, 0] dnsupdate\n'
        data = header + b'  Failed DNS update\n' + header
        self.assertEqual(completed_records(data, 0, 'samba'), [(header + b'  Failed DNS update\n', len(data) - len(header))])
        self.assertEqual(len(completed_records(data, 0, 'samba', len(data))), 2)
        result = transaction(initial_state(), snapshot({('samba', 'current'): data}))
        self.assertEqual(len(messages(result)), 1)
        self.assertIn('\n  Failed DNS update', messages(result)[0])

    def test_filtered_only_still_advances_and_unknown_survives(self):
        records = snapshot({('storage', 'current'): b'md_get_status: /dev/md1 : status=0, progress=100.000000.\nnew diagnostic\n'})
        result = prepare(initial_state(), records, 1_000_000_000)
        self.assertEqual(messages(result), ['new diagnostic'])
        committed = result['pending']['next']
        self.assertEqual(committed['normal'], {'storage/raid_healthy': 1})
        self.assertEqual(messages(prepare(committed, records, 2_000_000_000)), [])

    def test_entry_and_batch_bounds(self):
        records = snapshot({('kernel', 'current'): (b'\x01' * 30000 + b'\n') * 30})
        pending = transaction(initial_state(), records)
        for batch in pending['pending']['batches']:
            self.assertLess(len(json.dumps(batch).encode()), 256 * 1024)
            for stream in batch['streams']:
                for _, line in stream['values']:
                    self.assertLessEqual(len(line.encode()), 16384)
                    self.assertTrue(json.loads(line)['truncated'])


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'

    def test_failed_push_replays_identical_outbox_after_restart(self):
        records = snapshot({('kernel', 'current'): b'important\n'})
        sent = []
        def failing(batch):
            sent.append(copy.deepcopy(batch))
            raise OSError('not delivered')
        first = Collector('192.0.2.10', self.path, fetch=lambda _: records, sender=failing, clock=lambda: 1_000_000_000)
        first.poll_once()
        self.assertEqual(first.success, 0)
        self.assertEqual(first.state['cursors'], {})
        disk = load_state(self.path)
        self.assertIsNotNone(disk['pending'])
        def forbidden_fetch(_):
            self.fail('pending delivery must not fetch/advance source')
        retry = Collector('192.0.2.10', self.path, fetch=forbidden_fetch, sender=lambda batch: sent.append(copy.deepcopy(batch)), clock=lambda: 2_000_000_000)
        retry.poll_once()
        self.assertEqual(retry.success, 1)
        self.assertEqual(sent[0], sent[1])
        self.assertIsNone(load_state(self.path)['pending'])
        self.assertEqual(load_state(self.path)['sent'], 1)

    def test_corrupt_state_never_resets_or_sends(self):
        for value in ['broken', '{}', '{"version":1}', json.dumps({**initial_state(), 'last_ns': float('nan')})]:
            self.path.write_text(value)
            collector = Collector('192.0.2.10', self.path, fetch=lambda _: self.fail('must not fetch'), sender=lambda _: self.fail('must not send'))
            collector.poll_once()
            self.assertEqual(collector.success, 0)
            self.assertEqual(collector.state_valid, 0)
            self.assertEqual(self.path.read_text(), value)

    def test_strict_ssh(self):
        args = ssh_args('192.0.2.10')
        self.assertEqual(args[-1], 'nas-logs-v1')
        self.assertIn('StrictHostKeyChecking=yes', args)
        for host in ['localhost', '127.0.0.1', '8.8.8.8', '192.0.2.10;id']:
            with self.assertRaises(ValueError):
                ssh_args(host)


class NormalPatternTests(unittest.TestCase):
    def test_historical_positive_and_negative_contract(self):
        fixture = json.loads(Path(__file__).with_name('fixtures').joinpath('nas_normal_log_cases.json').read_text())
        for case in fixture['cases']:
            with self.subTest(case=case):
                actual = normal_pattern(case['kind'], case['message'], case['level'], fixture['normal_ssh_sources'][0])
                self.assertEqual(bool(actual), case['normal'])


if __name__ == '__main__':
    unittest.main()
