#!/usr/bin/env python3
"""Synthetic files/processes only; never sniff a real interface."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]/'files/security_capture.py'
spec = importlib.util.spec_from_file_location('security_capture', SOURCE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='capture-unit-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def private_json(self, name, value):
        path = self.root/name; path.write_text(json.dumps(value)); path.chmod(0o600)
        return path

    def test_fixed_profile_and_input_rejection(self):
        good = {'schema': 1, 'profiles': {'fixture': {'interface': 'br-lan', 'filter': 'icmp', 'duration_seconds': 1}}}
        path = self.private_json('config.json', good)
        self.assertEqual(module.config(path), good)
        for field, value in [('interface', 'eth0;touch bad'), ('filter', 'icmp\nextra'),
                             ('duration_seconds', True), ('duration_seconds', 181), ('duration_seconds', 1.5)]:
            bad = copy.deepcopy(good); bad['profiles']['fixture'][field] = value
            path = self.private_json('config.json', bad)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                module.config(path)
        bad = copy.deepcopy(good); bad['profiles']['../escape'] = bad['profiles'].pop('fixture')
        with self.assertRaises(ValueError):
            module.config(self.private_json('config.json', bad))
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            module.read_private(path)
        link = self.root/'config-link'; link.symlink_to(path)
        with self.assertRaises(ValueError):
            module.read_private(link)

    def frame(self, endian='<', included=b'fake', original=10):
        magic = b'\xd4\xc3\xb2\xa1' if endian=='<' else b'\xa1\xb2\xc3\xd4'
        header = magic+struct.pack(endian+'HHiIII',2,4,0,0,65535,1)
        return header+struct.pack(endian+'IIII',1,0,len(included),original)+included

    def test_closed_pcap_endianness_and_truncation(self):
        for endian in ['<', '>']:
            p = self.root/'capture.pcap'; p.write_bytes(self.frame(endian))
            result = module.pcap(p)
            self.assertEqual(result['packet_count'],1)
            self.assertEqual(result['truncated_packet_count'],1)
            self.assertEqual(result['size_bytes'],44)
            self.assertEqual(len(result['pcap_sha256']),64)

    def test_malformed_partial_and_oversized_pcap_rejected(self):
        valid = self.frame()
        cases = [b'bad', b'bad!'+valid[4:], valid[:-1], valid+b'partial', self.frame(original=1),
                 valid[:24]+valid[24:]*1001]
        for content in cases:
            p = self.root/'bad.pcap'; p.write_bytes(content)
            with self.subTest(size=len(content)), self.assertRaises(ValueError):
                module.pcap(p)
        with p.open('wb') as f:
            f.truncate(module.LIMIT+1)
        with self.assertRaises(ValueError):
            module.pcap(p)

    def test_atomic_manifest_and_exclusive_lock(self):
        p = self.root/'manifest.json'; module.publish(p, {'complete':True})
        self.assertEqual(module.read_private(p), {'complete':True})
        self.assertEqual(p.stat().st_mode & 0o777,0o600)
        self.assertEqual([x.name for x in self.root.iterdir()], ['manifest.json'])
        state = self.root/'state'
        with mock.patch.object(module,'STATE',state), module.lock():
            with self.assertRaises(BlockingIOError):
                module.lock()

    def test_stale_running_is_unknown_after_supervisor_death(self):
        state=self.root/'state'; state.mkdir(mode=0o700)
        module.publish(state/'last-result.json', {'status':'running','capture_id':'fixture'})
        with mock.patch.object(module,'STATE',state):
            with module.lock():
                self.assertEqual(module.status()['status'],'running')
            self.assertEqual(module.status()['status'],'unknown')
            module.publish(state/'last-result.json', {'status':'pending','capture_id':'new-fixture'})
            self.assertEqual(module.status()['status'],'pending')

    def test_serve_publishes_closed_manifest_and_preserves_failure(self):
        for fail in [False, True]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory(dir=self.root) as name:
                base = Path(name); state = base/'state'; state.mkdir(mode=0o700)
                spool = base/'spool'; spool.mkdir(mode=0o700)
                cfg = base/'config.json'; cfg.write_text('{}'); cfg.chmod(0o600)
                capture_id='20261031T000000Z-0123456789abcdef'
                module.publish(state/'request.json', {'schema':1,'capture_id':capture_id,'profile':'fixture',
                    'created_epoch':time.time(),'configuration_sha256':module.digest(cfg)})
                def fake_tcpdump(argv, duration):
                    Path(argv[argv.index('-w')+1]).write_bytes(self.frame())
                    return ('packet_limit' if fail else 'duration_limit'), 0
                profile = {'profiles':{'fixture':{'interface':'fixture0','filter':'icmp','duration_seconds':1}}}
                with mock.patch.object(module,'STATE',state), mock.patch.object(module,'SPOOL',spool), \
                     mock.patch.object(module,'CONFIG',cfg), mock.patch.object(module,'config',return_value=profile), \
                     mock.patch.object(module,'guards'), mock.patch.object(module,'offload_state',return_value={}), \
                     mock.patch.object(module.subprocess,'run',return_value=mock.Mock(returncode=0)), \
                     mock.patch.object(module,'run_tcpdump',side_effect=fake_tcpdump):
                    if fail:
                        with self.assertRaisesRegex(ValueError,'unexpected_early_exit'):
                            module.serve()
                    else:
                        module.serve()
                run=spool/capture_id
                if fail:
                    self.assertFalse((run/'manifest.json').exists())
                    self.assertTrue((run/'capture.pcap.part').exists())
                    self.assertEqual(module.read_private(run/'failed.json')['reason'],'unexpected_early_exit')
                else:
                    result=module.read_private(run/'manifest.json')
                    self.assertEqual(result['pcap_sha256'],module.digest(run/'capture.pcap'))
                    self.assertEqual(result['stop_reason'],'duration_limit')
                    self.assertEqual(result['packet_count'],1)
                    self.assertFalse((run/'capture.pcap.part').exists())
                self.assertFalse((state/'request.json').exists())

    def test_resource_guards_fail_closed(self):
        spool = self.root/'spool'
        with mock.patch.object(module.os.path,'ismount',return_value=False):
            with self.assertRaisesRegex(ValueError,'srv_not_mounted'):
                module.guards(spool)
        fs = mock.Mock(f_bavail=20*1024**3,f_frsize=1)
        with mock.patch.object(module.os.path,'ismount',return_value=True), \
             mock.patch.object(module.os,'statvfs',return_value=fs), \
             mock.patch.object(module.Path,'read_text',return_value='MemAvailable: 1048576 kB'), \
             mock.patch.object(module.os,'getloadavg',return_value=(0,0,0)):
            module.guards(spool)
            fs.f_bavail=8*1024**3
            with self.assertRaisesRegex(ValueError,'shared_storage_reserve'):
                module.guards(spool)
            fs.f_bavail=20*1024**3
            with mock.patch.object(module.os,'getloadavg',return_value=(3,0,0)):
                with self.assertRaisesRegex(ValueError,'router_load'):
                    module.guards(spool)
            with mock.patch.object(module.Path,'read_text',return_value='MemAvailable: 1000 kB'):
                with self.assertRaisesRegex(ValueError,'router_memory'):
                    module.guards(spool)
            (spool/'one').mkdir(); (spool/'two').mkdir()
            with self.assertRaisesRegex(ValueError,'spool_count_limit'):
                module.guards(spool)

    def test_offload_unknown_is_not_reported_disabled(self):
        with mock.patch.object(module.subprocess,'run',side_effect=FileNotFoundError):
            self.assertEqual(module.offload_state(), {'software_config':None,'hardware_config':None,'nft_flowtable_count':None})

    def test_supervisor_deadline_flush_and_error(self):
        script = self.root/'fake-tcpdump.py'
        script.write_text("import signal,time,sys\ndef done(*args):\n print('0 packets dropped by kernel',file=sys.stderr);sys.exit(0)\nsignal.signal(signal.SIGINT,done)\ntime.sleep(30)\n")
        started = time.monotonic()
        reason, drops = module.run_tcpdump([sys.executable,str(script)],.2)
        self.assertEqual((reason,drops),('duration_limit',0))
        self.assertLess(time.monotonic()-started,3)
        with self.assertRaises(ValueError):
            module.run_tcpdump([sys.executable,'-c','raise SystemExit(4)'],1)

    @unittest.skipUnless(sys.platform=='linux','Linux kernel parent-death contract')
    def test_kernel_stops_child_if_supervisor_is_killed(self):
        pidfile = self.root/'child.pid'
        child = self.root/'fake-child.py'
        child.write_text(f"import os,signal,time,pathlib\nsignal.signal(signal.SIGINT,signal.SIG_DFL)\npathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n")
        code = f"import importlib.util,sys\ns=importlib.util.spec_from_file_location('capture',{str(SOURCE)!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nm.run_tcpdump([sys.executable,{str(child)!r}],25)\n"
        parent = subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        child_pid = None
        child_fd = None
        try:
            deadline = time.monotonic()+5
            while not pidfile.exists():
                if time.monotonic()>deadline:
                    self.fail('synthetic child did not start')
                time.sleep(.02)
            child_pid = int(pidfile.read_text())
            child_fd = os.pidfd_open(child_pid)
            parent.kill(); parent.wait(timeout=3)
            self.assertTrue(select.select([child_fd], [], [], 3)[0], 'child survived supervisor death')
        finally:
            if parent.poll() is None:
                parent.kill(); parent.wait(timeout=3)
            if child_fd is not None:
                try:signal.pidfd_send_signal(child_fd,signal.SIGKILL)
                except ProcessLookupError:pass
                finally:os.close(child_fd)


if __name__=='__main__':
    unittest.main()
