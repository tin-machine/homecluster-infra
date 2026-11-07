"""SMART identity, persistence, failed reads and two-hour scheduling contracts."""
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import nas_smart as smart
from nas_file_logs_collector import ssh_args
from prod_nas_file_logs import rules
import test_prod_nas_file_logs as file_logs_tests


def snapshot(raw=1):
    lines = ['NAS_SMART_1']
    for bay in range(1, 5):
        lines.append(f'BAY {bay} {str(bay) * 64}')
        for attr in sorted(smart.REQUIRED):
            value = 39 if attr == 194 else raw if bay == 2 and attr == 188 else 0
            lines.append(f'{attr:03} Attribute {value} 100 099 000 OK')
        lines.append('END_BAY')
    return ('\n'.join(lines) + '\nEND\n').encode()


class SmartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'smart-state.json'
        self.payload = snapshot()
        self.collector = smart.Collector(ssh_args('192.0.2.10'), self.path,
                                         reader=lambda _: smart.parse_snapshot(self.payload), clock=lambda: 10000)

    def test_initial_existing_error_is_baseline(self):
        self.collector.poll_once()
        self.assertEqual(self.collector.success, 1)
        disk = smart.load_state(self.path)['disks']['2']
        self.assertEqual(disk['raw']['188'], 1)
        self.assertEqual(disk['increments']['188'], 0)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.collector.args[-1], 'nas-smart-v1')
        self.assertEqual(self.collector.args[:-1], ssh_args('192.0.2.10')[:-1])

    def test_unchanged_increment_reset_and_restart(self):
        for raw, expected in [(1, 0), (1, 0), (2, 1), (1, 1), (3, 3)]:
            self.payload = snapshot(raw)
            self.collector.poll_once()
            self.assertEqual(self.collector.state['disks']['2']['increments']['188'], expected)
        restarted = smart.Collector(self.collector.args, self.path,
                                    reader=lambda _: smart.parse_snapshot(snapshot(3)), clock=lambda: 17200)
        restarted.poll_once()
        self.assertEqual(restarted.state['disks']['2']['increments']['188'], 3)

    def test_replacement_starts_a_new_baseline(self):
        self.collector.poll_once()
        self.payload = snapshot(90).replace(b'2' * 64, b'a' * 64)
        self.collector.poll_once()
        disk = self.collector.state['disks']['2']
        self.assertEqual(disk['raw']['188'], 90)
        self.assertEqual(disk['increments']['188'], 0)
        self.assertEqual(disk['disk_id'], 'a' * 64)

    def test_reject_partial_duplicate_and_invalid_inputs(self):
        data = snapshot()
        bad = [b'', data[:-4], data + b'trailing\n', b'x' * (smart.MAX_BYTES + 1),
               data.replace(b'BAY 4', b'BAY 3'), data.replace(b'BAY 4', b'BAY 5'),
               data.replace(b'4' * 64, b'3' * 64), data.replace(b'4' * 64, b'bad'),
               data.replace(b'005 Attribute 0 100 099 000 OK\n', b'', 1),
               data.replace(b'005 Attribute 0', b'005 Attribute -1', 1),
               data.replace(b'005 Attribute 0', b'005 Attribute nan', 1),
               data.replace(b'005 Attribute 0', b'005 Attribute 9007199254740992', 1),
               data.replace(b'005 Attribute 0 100', b'005 Attribute 0 999', 1),
               data.replace(b'END_BAY', b'005 Attribute 0 100 099 000 OK\nEND_BAY', 1),
               data.replace(b'194 Attribute 39', b'194 Attribute 151', 1)]
        for item in bad:
            with self.subTest(item=item[:30]), self.assertRaises((ValueError, UnicodeError)):
                smart.parse_snapshot(item)

    def test_bad_status_and_threshold_are_observed_unhealthy(self):
        for data in [snapshot().replace(b'000 OK', b'000 FAIL', 1),
                     snapshot().replace(b'100 099 000 OK', b'100 099 100 OK', 1)]:
            self.assertEqual(smart.parse_snapshot(data)['1']['healthy'], 0)

    def test_failed_read_hides_old_disk_values_preserves_baseline(self):
        self.collector.poll_once()
        old = self.path.read_bytes()
        self.payload = b'incomplete'
        self.collector.poll_once()
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(self.collector.success, 0)
        self.assertEqual(self.collector.state_valid, 1)
        text = self.collector.metrics()
        self.assertNotIn('nas_smart_attribute_raw', text)
        self.assertIn('nas_smart_last_success_timestamp_seconds 10000', text)

    def test_corrupt_state_is_not_reset_or_overwritten(self):
        for content in [b'bad', b'{}', b'x' * (smart.MAX_BYTES + 1)]:
            self.path.write_bytes(content)
            self.collector.poll_once()
            self.assertEqual((self.collector.success, self.collector.state_valid), (0, 0))
            self.assertEqual(self.path.read_bytes(), content)

    def test_invalid_persisted_fields_are_rejected(self):
        self.collector.poll_once()
        base = smart.load_state(self.path)
        changes = [('last_success', float('nan')), ('version', 2), ('disks', {})]
        for key, value in changes:
            bad = copy.deepcopy(base); bad[key] = value
            with self.assertRaises(ValueError): smart.validate_state(bad)
        for field, value in [('raw', {}), ('increments', {'188': -1}), ('disk_id', 'bad'), ('healthy', 2)]:
            bad = copy.deepcopy(base); bad['disks']['1'][field] = value
            with self.assertRaises(ValueError): smart.validate_state(bad)

    def test_symlink_state_is_rejected(self):
        target = Path(self.tmp.name) / 'target'; target.write_text('untouched')
        self.path.symlink_to(target)
        self.collector.poll_once()
        self.assertEqual(self.collector.state_valid, 0)
        self.assertEqual(target.read_text(), 'untouched')

    def test_save_failure_is_not_published_as_success(self):
        self.collector.poll_once()
        self.payload = snapshot(2)
        with patch.object(self.collector, 'saver', side_effect=OSError('disk full')):
            self.collector.poll_once()
        self.assertEqual((self.collector.success, self.collector.state_valid), (0, 0))
        self.assertEqual(smart.load_state(self.path)['disks']['2']['increments']['188'], 0)
        self.assertNotIn('nas_smart_attribute_raw', self.collector.metrics())
        self.collector.poll_once()
        self.assertEqual(self.collector.state['disks']['2']['increments']['188'], 1)

    def test_error_after_replace_does_not_double_count(self):
        self.collector.poll_once()
        self.payload = snapshot(2)
        def saved_then_failed(path, state):
            smart.save_state(path, state)
            raise OSError('directory fsync failed')
        with patch.object(self.collector, 'saver', side_effect=saved_then_failed):
            self.collector.poll_once()
        self.assertEqual(self.collector.success, 0)
        self.collector.poll_once()
        self.assertEqual(self.collector.state['disks']['2']['increments']['188'], 1)

    def test_metrics_cardinality_and_identity(self):
        self.collector.poll_once()
        text = self.collector.metrics()
        self.assertEqual(sum(x.startswith('nas_smart_attribute_raw{') for x in text.splitlines()), 36)
        self.assertEqual(sum(x.startswith('nas_smart_attribute_increments_total{') for x in text.splitlines()), 36)
        self.assertEqual(sum(x.startswith('nas_smart_temperature_celsius{') for x in text.splitlines()), 4)
        self.assertIn('nas_smart_disk_healthy{bay="2",disk_id="' + '2' * 64 + '"} 1', text)

    def test_two_hour_schedule_and_shutdown(self):
        class Stop:
            stopped = False
            waits = []
            def is_set(self): return self.stopped
            def wait(self, seconds):
                self.waits.append(seconds); self.stopped = True
        stop = Stop()
        with patch.object(self.collector, 'poll_once') as poll:
            self.collector.run(stop)
            poll.assert_called_once()
        self.assertEqual(stop.waits, [7200])

    def test_fetch_bounds_nonzero_timeout_and_cleanup(self):
        for payload, code, timedout in [(snapshot(), 0, False), (b'x' * 65537, 0, False),
                                       (snapshot(), 1, False), (snapshot(), 0, True)]:
            class Process:
                stdout = io.BytesIO(payload)
                killed = False
                calls = 0
                def poll(self): return None if timedout and not self.killed else code
                def kill(self): self.killed = True
                def wait(self, timeout=None):
                    self.calls += 1
                    if timedout and self.calls == 1: raise subprocess.TimeoutExpired('ssh', 2)
                    return code
            proc = Process()
            with patch.object(smart.subprocess, 'Popen', return_value=proc), patch.object(smart.threading, 'Timer') as timer:
                if code or timedout or len(payload) > 65536:
                    with self.assertRaises((ValueError, subprocess.SubprocessError)): smart.fetch(['ssh'])
                else:
                    self.assertEqual(len(smart.fetch(['ssh'])), 4)
                timer.assert_called_once_with(20, proc.kill)
                self.assertTrue(proc.stdout.closed)
                timer.return_value.cancel.assert_called_once()
                if timedout: self.assertTrue(proc.killed)

    def test_rendered_rules_scrape_and_dashboard(self):
        objects = file_logs_tests.IntegrationTests().render(True, True)
        config = json.loads(objects['ConfigMap/prod-prometheus-config']['data']['prometheus.json'])
        job = next(x for x in config['scrape_configs'] if x['job_name'] == 'qnap-nas-file-logs')
        self.assertEqual(job['scrape_interval'], '30s')
        self.assertIn('nas_smart_.*', job['metric_relabel_configs'][0]['regex'])
        alerts = {x['alert']: x for x in rules()['groups'][0]['rules']}
        self.assertIn('[6h]', alerts['NASSMARTAttributeIncreased']['expr'])
        self.assertIn('9000', alerts['NASSMARTCollectionUnavailable']['expr'])
        self.assertIn('absent(', alerts['NASSMARTCollectionUnavailable']['expr'])
        panels = json.loads(objects['ConfigMap/prod-grafana-provisioning']['data']['qnap-nas.json'])['panels']
        self.assertEqual(panels[-1]['type'], 'logs')
        for i, a in enumerate(panels):
            for b in panels[i + 1:]:
                x, y = a['gridPos'], b['gridPos']
                overlaps = x['x'] < y['x'] + y['w'] and y['x'] < x['x'] + x['w'] and x['y'] < y['y'] + y['h'] and y['y'] < x['y'] + x['h']
                self.assertFalse(overlaps, (a['title'], b['title']))


if __name__ == '__main__':
    unittest.main()
