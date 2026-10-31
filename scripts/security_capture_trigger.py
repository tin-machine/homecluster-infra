#!/usr/bin/env python3
"""Confirmed operator-side capture authorization. Automatic dispatch stays off.

Private identity bindings and the dedicated authorizer key are supplied through
an owner-only configuration file. Existing transfer/analysis commands remain
separate; settle requires a successful NAS analysis and destination-bound Loki
delivery. Uncertain starts cannot be retried with this receipt.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time
import uuid

from security_lab_transfer import Client, SSH, TransferError, TARGET, NAME, HEX, CAPTURE, private_file, private_parent, require, save_receipt, json_response

LOKI_GUARD = r'''
import ipaddress,json,subprocess,sys,time,urllib.parse,urllib.request
p=json.load(sys.stdin)
k=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml']
def get(*a):return json.loads(subprocess.check_output(k+['get',*a,'-o','json'],text=True))
assert get('namespace','kube-system')['metadata']['uid']==p['cluster_uid']
host=str(ipaddress.ip_address(get('service','prod-loki','-n','observability-prod')['spec']['clusterIP']))
if ':' in host:host='['+host+']'
now=time.time()
query='{service_name="openwrt-security"} | appname="homecluster-security" | line_format "{{.message}}"'
args=urllib.parse.urlencode({'query':query,'start':str(int((now-180)*10**9)),'end':str(int((now+5)*10**9)),
                           'limit':2,'direction':'backward'})
with urllib.request.urlopen('http://'+host+':3100/loki/api/v1/query_range?'+args,timeout=15) as r:
 raw=r.read(262145)
assert len(raw)<=262144
d=json.loads(raw);assert d['status']=='success' and d['data']['resultType']=='streams'
rows=[]
for stream in d['data']['result']:
 for entry in stream.get('values',[]):
  metadata=dict(stream['stream'])
  if len(entry)>2:metadata.update(entry[2])
  rows.append((int(entry[0]),metadata,entry[1]))
assert rows
rows.sort(key=lambda x:x[0],reverse=True)
assert len(rows)==1 or rows[0][0]!=rows[1][0]
stamp,metadata,body=rows[0]
assert 0<=now-stamp/10**9<=180
assert metadata.get('security_parser_status')=='ok' and metadata.get('sensor')==p['sensor']
assert metadata.get('log_type')=='heartbeat' and metadata.get('environment')=='staging'
value=json.loads(body)
assert value['schema']==1 and value['sensor']==p['sensor'] and value['log_type']=='heartbeat'
assert value['security_source']=='openwrt' and value['banip_status']=='active'
assert not value.get('banip_error') and not value.get('banip_feed_error')
assert value['banip_feed_verification']=='verified'
age=value['banip_feed_max_age_seconds'];assert type(age) in (int,float) and 0<=age<=172800
from datetime import datetime
created=datetime.fromisoformat(value['timestamp']);assert created.tzinfo is not None
assert 0<=now-created.timestamp()<=180
print(json.dumps({'status':'ready','heartbeat_epoch':stamp/10**9,'observed_at':now}))
'''


def configuration(path):
    private_file(path)
    value = json.loads(path.read_text())
    require(isinstance(value, dict) and set(value) == {'schema', 'environment', 'automatic', 'server', 'cluster_uid',
            'namespace_uid', 'worker', 'router', 'trigger_key', 'loki_reader', 'loki_cluster_uid', 'sensor', 'profiles'} and
            type(value['schema']) is int and value['schema'] == 1 and value['environment'] == 'staging' and
            value['automatic'] is False, 'trigger_client_configuration')
    require(isinstance(value['router'], str) and TARGET.fullmatch(value['router']) and value['router'].startswith('root@') and
            isinstance(value['loki_reader'], str) and TARGET.fullmatch(value['loki_reader']) and
            isinstance(value['sensor'], str) and NAME.fullmatch(value['sensor']), 'trigger_client_binding')
    require(str(uuid.UUID(value['loki_cluster_uid'])) == value['loki_cluster_uid'], 'trigger_loki_uid')
    require(isinstance(value['profiles'], list) and 1 <= len(value['profiles']) <= 8 and
            all(isinstance(x, str) and NAME.fullmatch(x) for x in value['profiles']) and
            len(set(value['profiles'])) == len(value['profiles']), 'trigger_client_profiles')
    key = Path(value['trigger_key'])
    require(key.is_absolute(), 'trigger_key_absolute')
    private_file(key)
    return value


class Authorizer:
    def __init__(self, value, client=None):
        self.value = value
        self.client = client or Client(value['server'], value['cluster_uid'], value['namespace_uid'], value['worker'])

    def router(self, request):
        value = self.value
        key = Path(value['trigger_key'])
        private_file(key)
        args = SSH + ['-F', '/dev/null', '-oIdentitiesOnly=yes', '-oIdentityAgent=none', '-i', str(key),
                      value['router'], 'homecluster-security-trigger-v1']
        result = json_response(self.client.transport(args, data=json.dumps(request, allow_nan=False).encode(), limit=32768))
        require(isinstance(result, dict) and result.get('status') not in (None, 'failed'), 'trigger_router_refused')
        return result

    def observation(self):
        context = self.client.context()
        evidence = self.client.runtime(context, 'trigger-readiness')
        require(evidence.get('status') == 'ready' and evidence.get('cluster_uid') == self.value['cluster_uid'] and
                evidence.get('worker') == self.value['worker'] and evidence.get('images_cached') == 3 and
                isinstance(evidence.get('configuration_sha256'), str) and HEX.fullmatch(evidence['configuration_sha256']) and
                evidence.get('automatic') is False, 'trigger_pipeline_not_ready')
        body = {'cluster_uid': self.value['loki_cluster_uid'], 'sensor': self.value['sensor']}
        raw = self.client.transport(SSH + [self.value['loki_reader'], shlex.join(['sudo', 'python3', '-c', LOKI_GUARD])],
                                    data=json.dumps(body).encode(), limit=4096)
        monitor = json_response(raw)
        now = time.time()
        require(monitor.get('status') == 'ready' and type(monitor.get('observed_at')) in (int, float) and
                0 <= now - monitor['observed_at'] <= 30 and type(monitor.get('heartbeat_epoch')) in (int, float) and
                0 <= now - monitor['heartbeat_epoch'] <= 180, 'trigger_monitor_not_ready')
        return {'coordinator_uid': context['pod_uid'], 'configuration_sha256': evidence['configuration_sha256'],
                'guarded_at': now, 'heartbeat_epoch': monitor['heartbeat_epoch']}

    def message(self, action):
        return {'schema': 1, 'action': action, 'nonce': uuid.uuid4().hex, 'created_at': time.time()}

    def plan(self, profile, path, config_hash):
        require(profile in self.value['profiles'] and not path.exists(), 'trigger_plan_profile_or_receipt')
        observed = self.observation()
        request = dict(self.message('plan'), profile=profile)
        result = self.router(request)
        require(result.get('status') == 'planned' and result.get('profile') == profile and
                result.get('nonce') == request['nonce'] and isinstance(result.get('plan_id'), str) and
                HEX.fullmatch(result['plan_id']) and result.get('expires_at') == request['created_at'] + 120,
                'trigger_plan_response')
        receipt = {'schema': 1, 'status': 'planned', 'configuration_sha256': config_hash, 'request': request,
                   'plan': result, 'observed': observed}
        save_receipt(path, receipt)
        return {'status': 'planned', 'plan_id': result['plan_id'], 'profile': profile,
                'target': result['target'], 'expires_at': result['expires_at'], 'automatic': False}

    def start(self, path, confirm, config_hash):
        private_file(path)
        receipt = json.loads(path.read_text())
        require(receipt.get('status') == 'planned' and receipt.get('configuration_sha256') == config_hash and
                confirm == receipt['plan']['plan_id'] and HEX.fullmatch(confirm), 'trigger_confirmation_or_receipt')
        require(0 <= time.time() - receipt['request']['created_at'] <= 120, 'trigger_plan_expired')
        observed = self.observation()
        require(all(observed[k] == receipt['observed'][k] for k in ('coordinator_uid', 'configuration_sha256')),
                'trigger_observation_identity_changed')
        request = dict(receipt['request'], action='start', plan_id=confirm, guarded_at=observed['guarded_at'])
        receipt.update(status='start_attempted', observed=observed)
        save_receipt(path, receipt)  # Unknown transport result must not be auto-retried.
        result = self.router(request)
        require(result.get('status') == 'accepted' and result.get('plan_id') == confirm and
                isinstance(result.get('capture_id'), str) and
                CAPTURE.fullmatch(result['capture_id']), 'trigger_start_response')
        receipt.update(status='accepted', result=result)
        save_receipt(path, receipt)
        return result

    def settle(self, path, transfer_path, config_hash):
        private_file(path)
        receipt = json.loads(path.read_text())
        require(receipt.get('configuration_sha256') == config_hash and receipt.get('status') in ('accepted', 'start_attempted'),
                'trigger_settlement_receipt')
        current = self.router(self.message('status'))
        active = current.get('active')
        settled = current.get('last_settled')
        if current.get('status') == 'idle' and isinstance(settled, dict) and settled.get('plan_id') == receipt['plan']['plan_id']:
            receipt.update(status='settled', result=dict(status='settled', **settled))
            save_receipt(path, receipt)
            return receipt['result']
        require(current.get('status') == 'unsettled' and isinstance(active, dict) and
                active.get('plan_id') == receipt['plan']['plan_id'] and active.get('capture_id'), 'trigger_active_identity')
        private_file(transfer_path)
        transfer = json.loads(transfer_path.read_text())
        self.client.bind_receipt(transfer)
        require(transfer.get('router') == self.value['router'] and transfer.get('capture_id') == active['capture_id'] and
                transfer.get('router_ack') == 'acknowledged' and transfer.get('retention_source') == 'confirmed',
                'trigger_transfer_not_confirmed')
        context = self.client.context()
        self.client.verify_receipt_input(context, transfer)
        delivery = self.client.check_delivery(transfer_path, self.value['loki_reader'], self.value['loki_cluster_uid'], 0)
        require(delivery.get('status') == 'delivered' and delivery.get('analysis_status') == 'succeeded',
                'trigger_analysis_or_delivery_unfinished')
        request = dict(self.message('settle'), plan_id=active['plan_id'], capture_id=active['capture_id'],
                       analysis_id=transfer['analysis_id'], guarded_at=time.time())
        result = self.router(request)
        require(result.get('status') == 'settled' and result.get('plan_id') == active['plan_id'] and
                result.get('capture_id') == active['capture_id'], 'trigger_settlement_response')
        receipt.update(status='settled', result=result)
        save_receipt(path, receipt)
        return result


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'start', 'status', 'settle'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--profile')
    parser.add_argument('--confirm')
    parser.add_argument('--transfer-receipt', type=Path)
    args = parser.parse_args(argv)
    config = configuration(args.config)
    authorizer = Authorizer(config)
    config_hash = hashlib.sha256(args.config.read_bytes()).hexdigest()
    if args.action == 'status':
        require(not any((args.receipt, args.profile, args.confirm, args.transfer_receipt)), 'trigger_status_arguments')
        print(json.dumps(authorizer.router(authorizer.message('status')), sort_keys=True))
        return
    require(args.receipt is not None, 'trigger_receipt_required')
    private_parent(args.receipt)
    fd = os.open(args.receipt.with_name(args.receipt.name + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        private_file(args.receipt.with_name(args.receipt.name + '.lock'))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'plan':
            require(args.profile is not None and not args.confirm and not args.transfer_receipt, 'trigger_plan_arguments')
            result = authorizer.plan(args.profile, args.receipt, config_hash)
        elif args.action == 'start':
            require(args.confirm is not None and not args.profile and not args.transfer_receipt, 'trigger_start_arguments')
            result = authorizer.start(args.receipt, args.confirm, config_hash)
        else:
            require(args.transfer_receipt is not None and not args.profile and not args.confirm, 'trigger_settle_arguments')
            result = authorizer.settle(args.receipt, args.transfer_receipt, config_hash)
        print(json.dumps(result, sort_keys=True))
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'reason': str(error) if isinstance(error, TransferError) else type(error).__name__}))
        sys.exit(1)
