#!/usr/bin/env python3
"""No network/capture: exercise the durable authenticated admission protocol."""
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'files/security_trigger.py'
spec = importlib.util.spec_from_file_location('trigger', SOURCE)
trigger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trigger)
NOW = 1_800_000_000.0


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.state = self.root / 'state.json'
        self.initial = {'schema': 1, 'last_epoch': NOW - 1, 'events': [], 'active': None, 'last_settled': None}
        self.config = {'schema': 1, 'automatic': False, 'profiles': {'fixture': 'fixture-host'}}
        self.save(self.initial)
        self.patches = [patch.object(trigger, 'ROOT', self.root),
                        patch.object(trigger, 'configuration', return_value=self.config),
                        patch.object(trigger.cap, 'digest', return_value='a' * 64),
                        patch.object(trigger.os.path, 'ismount', return_value=True),
                        patch.object(trigger.cap, 'lock', side_effect=contextlib.nullcontext),
                        patch.object(trigger.cap, 'guards'),
                        patch.object(trigger.cap, 'STATE', self.root),
                        patch.object(trigger.cap, 'status', return_value={'status': 'complete'})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def save(self, value):
        self.state.write_text(json.dumps(value)); self.state.chmod(0o600)

    def request(self, action='plan', nonce='1' * 32, at=NOW):
        return {'schema': 1, 'action': action, 'nonce': nonce, 'created_at': at,
                **({'profile': 'fixture'} if action in ('plan', 'start') else {})}

    def start(self, fail=False):
        request = self.request()
        planned = trigger.dispatch(request, NOW)
        request.update(action='start', plan_id=planned['plan_id'], guarded_at=NOW)
        def launch(profile, expected_sha256, planned_capture_id):
            state = json.loads(self.state.read_text())
            self.assertEqual(len(state['events']), 1)
            self.assertEqual(state['active']['capture_id'], planned_capture_id)
            self.assertEqual(expected_sha256, 'a' * 64)
            if fail:
                raise TimeoutError('synthetic ambiguous launch')
            print(json.dumps({'status': 'accepted', 'capture_id': planned_capture_id}))
        with patch.object(trigger.cap, 'request', side_effect=launch) as launched:
            result = trigger.dispatch(request, NOW)
            self.assertEqual(launched.call_count, 1)
        return request, result

    def test_plan_has_no_reservation_and_start_reserves_before_launch(self):
        before = self.state.read_bytes()
        trigger.dispatch(self.request(), NOW)
        self.assertEqual(before, self.state.read_bytes())
        request, result = self.start()
        self.assertEqual(result['status'], 'accepted')
        with self.assertRaisesRegex(ValueError, 'trigger_replay'):
            trigger.dispatch(request, NOW + 1)
        other = self.request(nonce='2' * 32)
        with self.assertRaisesRegex(ValueError, 'trigger_unsettled'):
            trigger.dispatch(other, NOW + 1)

    def test_ambiguous_start_keeps_persisted_capture_identity(self):
        with self.assertRaises(TimeoutError):
            self.start(fail=True)
        state = trigger.load_state()
        self.assertIsNotNone(state['active']['capture_id'])
        with self.assertRaisesRegex(ValueError, 'trigger_unsettled'):
            trigger.dispatch(self.request(nonce='2' * 32), NOW + 5)

    def test_request_schema_time_command_and_profile(self):
        bad = [dict(self.request(), schema=True), dict(self.request(), action='shell'),
               dict(self.request(), filter='arbitrary'), dict(self.request(), created_at=NOW - 121),
               dict(self.request(), created_at=NOW + 1), dict(self.request(), created_at=float('nan')),
               dict(self.request(), nonce='../escape'), dict(self.request(), profile='unlisted')]
        with patch.object(trigger.cap, 'request') as launch:
            for value in bad:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    trigger.dispatch(value, NOW)
            launch.assert_not_called()
        for raw in ('{"action":"plan","action":"start"}', '{"created_at":NaN}'):
            with self.assertRaises(ValueError):
                trigger.strict_json(raw)
        result = subprocess.run([sys.executable, '-B', str(SOURCE), 'ssh'], input=b'{}',
                                env=dict(os.environ, SSH_ORIGINAL_COMMAND='arbitrary shell'), capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['reason'], 'trigger_forced_command')

    def test_plan_config_and_observation_changes_rejected_without_reservation(self):
        plan = trigger.dispatch(self.request(), NOW)
        request = dict(self.request('start'), plan_id=plan['plan_id'], guarded_at=NOW)
        for change in ('digest', 'target', 'observation'):
            with self.subTest(change=change):
                with contextlib.ExitStack() as stack:
                    if change == 'digest':
                        stack.enter_context(patch.object(trigger.cap, 'digest', return_value='b' * 64))
                    elif change == 'target':
                        stack.enter_context(patch.object(trigger, 'configuration', return_value={
                            **self.config, 'profiles': {'fixture': 'other-host'}}))
                    else:
                        request['guarded_at'] = NOW - 31
                    with self.assertRaisesRegex(ValueError, 'trigger_plan_or_observation_changed'):
                        trigger.dispatch(request, NOW)
                self.assertIsNone(trigger.load_state()['active'])

    def test_rolling_limits_and_clock_rollback(self):
        cases = [('trigger_target_cooldown', [(100, 'fixture-host')]),
                 ('trigger_hourly_limit', [(10, 'a'), (100, 'b'), (200, 'c')]),
                 ('trigger_daily_limit', [(4000 + i * 100, 'other') for i in range(10)])]
        for reason, items in cases:
            value = copy.deepcopy(self.initial)
            value['events'] = [{'at': NOW - age, 'target': target, 'nonce': f'{i+10:032x}'}
                               for i, (age, target) in enumerate(items)]
            self.save(value)
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                trigger.dispatch(self.request(), NOW)
        self.save(dict(self.initial, last_epoch=NOW + 1))
        with self.assertRaisesRegex(ValueError, 'trigger_clock_rollback'):
            trigger.dispatch(self.request(), NOW)
        value = copy.deepcopy(self.initial)
        value['events'] = [{'at': NOW - 86401, 'target': 'fixture-host', 'nonce': 'a' * 32}]
        self.save(value)
        self.assertEqual(trigger.dispatch(self.request(), NOW)['status'], 'planned')

    def test_storage_and_existing_capture_fail_before_reservation(self):
        for reason in ('router_load', 'router_memory', 'shared_storage_reserve', 'spool_count_limit'):
            with self.subTest(reason=reason), patch.object(trigger.cap, 'guards', side_effect=trigger.cap.CaptureError(reason)):
                with self.assertRaisesRegex(ValueError, reason):
                    trigger.dispatch(self.request(), NOW)
            self.assertIsNone(trigger.load_state()['active'])
        (self.root / 'request.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'request_pending'):
            trigger.dispatch(self.request(), NOW)

    def test_missing_corrupt_linked_and_insecure_state_never_reset(self):
        for value in ({}, dict(self.initial, events=[{}]), dict(self.initial, active={}),
                      dict(self.initial, last_epoch=True)):
            self.save(value)
            with self.assertRaises(ValueError):
                trigger.dispatch(self.request(), NOW)
        self.save(self.initial)
        self.state.chmod(0o644)
        with self.assertRaises(ValueError):
            trigger.load_state()
        self.state.chmod(0o600)
        os.link(self.state, self.root / 'hardlink')
        with self.assertRaises(ValueError):
            trigger.load_state()
        self.state.unlink()
        with self.assertRaises(FileNotFoundError):
            trigger.load_state()

    def test_settle_requires_source_ack_and_keeps_rate_history(self):
        request, result = self.start()
        settle = dict(self.request('settle', nonce='b' * 32), plan_id=result['plan_id'],
                      capture_id=result['capture_id'], analysis_id='c' * 64, guarded_at=NOW)
        with patch.object(trigger.cap, 'read_private', return_value={}), self.assertRaisesRegex(ValueError, 'source_not_acknowledged'):
            trigger.dispatch(settle, NOW)
        with patch.object(trigger.cap, 'read_private', return_value={'status': 'acknowledged', 'capture_id': result['capture_id']}):
            self.assertEqual(trigger.dispatch(settle, NOW)['status'], 'settled')
        state = trigger.load_state()
        self.assertEqual(len(state['events']), 1)
        self.assertEqual(state['last_settled']['plan_id'], result['plan_id'])
        with self.assertRaisesRegex(ValueError, 'trigger_target_cooldown'):
            trigger.dispatch(self.request(nonce='2' * 32), NOW + 10)


if __name__ == '__main__':
    unittest.main()
