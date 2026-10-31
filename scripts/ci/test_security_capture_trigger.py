#!/usr/bin/env python3
"""Operator authorization and actual Loki guard code, using synthetic endpoints."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import security_capture_trigger as trigger


class AuthorizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name); self.root.chmod(0o700)
        self.key = self.root / 'fixture-key'; self.key.write_text('synthetic key never used'); self.key.chmod(0o600)
        self.config = {'schema': 1, 'environment': 'staging', 'automatic': False,
                       'server': 'operator@stg.invalid', 'cluster_uid': '00000000-0000-0000-0000-000000000001',
                       'namespace_uid': '00000000-0000-0000-0000-000000000002', 'worker': 'fixture-worker',
                       'router': 'root@router.invalid', 'trigger_key': str(self.key), 'loki_reader': 'reader@prod.invalid',
                       'loki_cluster_uid': '00000000-0000-0000-0000-000000000003', 'sensor': 'fixture-router',
                       'profiles': ['fixture']}
        self.client = unittest.mock.Mock()
        self.authorizer = trigger.Authorizer(self.config, client=self.client)
        self.observed = {'guarded_at': time.time(), 'heartbeat_epoch': time.time(),
                         'coordinator_uid': 'pod-original', 'configuration_sha256': 'a' * 64}
        self.path = self.root / 'receipt.json'
        self.config_hash = 'b' * 64

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        def router(request):
            return dict(request, status='planned', plan_id='c' * 64, target='fixture-host',
                        expires_at=request['created_at'] + 120)
        with patch.object(self.authorizer, 'observation', return_value=self.observed), \
                patch.object(self.authorizer, 'router', side_effect=router):
            return self.authorizer.plan('fixture', self.path, self.config_hash)

    def test_owner_only_config_no_automatic_mode(self):
        path = self.root / 'config.json'
        for field, value in ((None, None), ('automatic', True), ('environment', 'prod'),
                             ('router', 'root@router.invalid;bad'), ('profiles', ['fixture', 'fixture'])):
            config = dict(self.config)
            if field:
                config[field] = value
            path.write_text(json.dumps(config)); path.chmod(0o600)
            if field is None:
                self.assertEqual(trigger.configuration(path), self.config)
            else:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    trigger.configuration(path)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            trigger.configuration(path)

    def test_start_requires_exact_confirmation_and_fresh_same_identity(self):
        plan = self.plan()
        with patch.object(self.authorizer, 'router') as remote:
            for confirm, config_hash, observed in (
                ('d' * 64, self.config_hash, self.observed),
                (plan['plan_id'], 'e' * 64, self.observed),
                (plan['plan_id'], self.config_hash, dict(self.observed, coordinator_uid='pod-new')),
                (plan['plan_id'], self.config_hash, dict(self.observed, configuration_sha256='f' * 64))):
                with patch.object(self.authorizer, 'observation', return_value=observed), self.assertRaises(ValueError):
                    self.authorizer.start(self.path, confirm, config_hash)
            remote.assert_not_called()

    def test_ambiguous_transport_is_recorded_and_never_retried(self):
        plan = self.plan()
        def lost(request):
            self.assertEqual(json.loads(self.path.read_text())['status'], 'start_attempted')
            raise trigger.TransferError('synthetic_transport_lost')
        with patch.object(self.authorizer, 'observation', return_value=self.observed), \
                patch.object(self.authorizer, 'router', side_effect=lost) as remote:
            with self.assertRaises(ValueError):
                self.authorizer.start(self.path, plan['plan_id'], self.config_hash)
            with self.assertRaisesRegex(ValueError, 'confirmation_or_receipt'):
                self.authorizer.start(self.path, plan['plan_id'], self.config_hash)
            self.assertEqual(remote.call_count, 1)

    def test_pipeline_unknown_or_monitor_failure_never_plans_capture(self):
        self.client.context.return_value = {'pod_uid': 'pod-original'}
        valid = {'status': 'ready', 'cluster_uid': self.config['cluster_uid'], 'worker': self.config['worker'],
                 'images_cached': 3, 'configuration_sha256': 'a' * 64, 'automatic': False}
        for evidence in ({}, dict(valid, status='unknown'), dict(valid, images_cached=2)):
            self.client.runtime.return_value = evidence
            with self.assertRaisesRegex(ValueError, 'pipeline_not_ready'):
                self.authorizer.plan('fixture', self.path, self.config_hash)
            self.assertFalse(self.path.exists())
        self.client.runtime.return_value = valid
        self.client.transport.side_effect = trigger.TransferError('synthetic_monitor_down')
        with self.assertRaisesRegex(ValueError, 'monitor_down'):
            self.authorizer.plan('fixture', self.path, self.config_hash)
        self.assertFalse(self.path.exists())

    def test_dedicated_key_transport_is_fixed_and_bounded(self):
        self.client.transport.return_value = b'{"status":"idle"}'
        self.authorizer.router(self.authorizer.message('status'))
        args = self.client.transport.call_args.args[0]
        self.assertIn('-oIdentityAgent=none', args)
        self.assertIn('-oStrictHostKeyChecking=yes', args)
        self.assertIn('-oIdentitiesOnly=yes', args)
        self.assertEqual(args[-2:], [self.config['router'], 'homecluster-security-trigger-v1'])
        self.assertEqual(self.client.transport.call_args.kwargs['limit'], 32768)

    def test_settlement_requires_analysis_success_and_destination_delivery(self):
        self.plan()
        receipt = json.loads(self.path.read_text()); receipt['status'] = 'start_attempted'
        trigger.save_receipt(self.path, receipt)
        cid = '20261031T000000Z-0123456789abcdef'
        active = {'plan_id': 'c' * 64, 'capture_id': cid}
        transfer_path = self.root / 'transfer.json'
        transfer = {'capture_id': cid, 'analysis_id': 'd' * 64, 'router': self.config['router'],
                    'router_ack': 'acknowledged', 'retention_source': 'confirmed'}
        transfer_path.write_text(json.dumps(transfer)); transfer_path.chmod(0o600)
        for delivery in ({'status': 'pending'}, {'status': 'delivered', 'analysis_status': 'failed'}):
            self.client.check_delivery.return_value = delivery
            with patch.object(self.authorizer, 'router', return_value={'status': 'unsettled', 'active': active}) as remote:
                with self.assertRaisesRegex(ValueError, 'analysis_or_delivery_unfinished'):
                    self.authorizer.settle(self.path, transfer_path, self.config_hash)
                self.assertEqual(remote.call_count, 1)
        self.client.check_delivery.return_value = {'status': 'delivered', 'analysis_status': 'succeeded'}
        def router(request):
            if request['action'] == 'status':
                return {'status': 'unsettled', 'active': active}
            self.assertEqual(request['action'], 'settle')
            self.client.verify_receipt_input.assert_called()
            self.client.check_delivery.assert_called_with(transfer_path, self.config['loki_reader'], self.config['loki_cluster_uid'], 0)
            return dict(status='settled', **active)
        with patch.object(self.authorizer, 'router', side_effect=router):
            self.assertEqual(self.authorizer.settle(self.path, transfer_path, self.config_hash)['status'], 'settled')
        self.assertEqual(json.loads(self.path.read_text())['status'], 'settled')

    def test_lost_settle_response_recovers_only_matching_durable_plan(self):
        self.plan()
        receipt = json.loads(self.path.read_text()); receipt['status'] = 'accepted'
        trigger.save_receipt(self.path, receipt)
        settled = {'plan_id': 'c' * 64, 'capture_id': '20261031T000000Z-0123456789abcdef', 'analysis_id': 'd' * 64}
        with patch.object(self.authorizer, 'router', return_value={'status': 'idle', 'active': None, 'last_settled': settled}):
            self.assertEqual(self.authorizer.settle(self.path, self.root / 'unused', self.config_hash)['status'], 'settled')
        self.client.runtime.assert_not_called()


class LokiGuardTests(unittest.TestCase):
    def run_guard(self, change=None):
        now = time.time()
        from datetime import datetime, timezone
        metadata = {'security_parser_status': 'ok', 'sensor': 'fixture-router', 'log_type': 'heartbeat', 'environment': 'staging'}
        body = {'schema': 1, 'sensor': 'fixture-router', 'log_type': 'heartbeat', 'security_source': 'openwrt',
                'banip_status': 'active', 'banip_feed_verification': 'verified', 'banip_feed_max_age_seconds': 2,
                'timestamp': datetime.fromtimestamp(now, timezone.utc).isoformat()}
        entry = [str(int(now * 10**9)), json.dumps(body), metadata]
        response = {'status': 'success', 'data': {'resultType': 'streams', 'result': [{'stream': {}, 'values': [entry]}]}}
        if change:
            change(response, metadata, body, entry)
            entry[1] = json.dumps(body)
        class Reader:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, *_): return json.dumps(response).encode()
        def kube(args, **_):
            if 'namespace' in args:
                return json.dumps({'metadata': {'uid': 'cluster-fixture'}})
            return json.dumps({'spec': {'clusterIP': '192.0.2.1'}})
        import io, contextlib, urllib.request
        output = io.StringIO()
        with patch.object(subprocess, 'check_output', side_effect=kube), \
                patch.object(urllib.request, 'urlopen', return_value=Reader()), \
                patch.object(sys, 'stdin', io.StringIO('{"cluster_uid":"cluster-fixture","sensor":"fixture-router"}')), \
                contextlib.redirect_stdout(output):
            exec(compile(trigger.LOKI_GUARD, '<fixed-loki-guard>', 'exec'), {})
        return json.loads(output.getvalue())

    def test_actual_guard_rejects_stale_invalid_unknown_and_wrong_environment(self):
        self.assertEqual(self.run_guard()['status'], 'ready')
        changes = [lambda r,m,b,e: m.update(security_parser_status='invalid'),
                   lambda r,m,b,e: m.update(environment='prod'),
                   lambda r,m,b,e: b.update(banip_status='unknown'),
                   lambda r,m,b,e: b.update(banip_feed_verification='unknown'),
                   lambda r,m,b,e: b.update(banip_feed_max_age_seconds=True),
                   lambda r,m,b,e: e.__setitem__(0, str(int((time.time()-181)*10**9))),
                   lambda r,m,b,e: e.__setitem__(0, str(int((time.time()+60)*10**9))),
                   lambda r,m,b,e: r['data'].update(result=[]),
                   lambda r,m,b,e: b.update(timestamp='invalid')]
        for index, change in enumerate(changes):
            with self.subTest(index=index), self.assertRaises((AssertionError, ValueError)):
                self.run_guard(change)


if __name__ == '__main__':
    unittest.main()
