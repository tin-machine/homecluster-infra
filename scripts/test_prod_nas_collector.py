import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from nas_nfs_collector import Collector, parse_nfsd, ssh_args, POLL_INTERVAL


class CollectorTests(unittest.TestCase):
    def test_numeric_records_and_invalid_inputs(self):
        self.assertEqual(parse_nfsd('proc3 1 42\nproc4 2 7 100\n'), 100)
        self.assertEqual(parse_nfsd('proc4 2 0 0'), 0)
        for s in ['', 'proc4 2 0 -1', 'proc4 2 0 1.1', 'proc4 2 0 1\nproc4 2 0 2', 'proc4 3 0 1', 'proc4 2 0 1 extra', 'proc4 2 0 １２', 'x'*65537]:
            with self.subTest(s=s[:50]), self.assertRaises(ValueError):parse_nfsd(s)

    def test_failure_retention_reset_recovery_and_no_scrape_polling(self):
        runner=Mock(side_effect=[SimpleNamespace(stdout='proc4 2 0 100'), subprocess.TimeoutExpired('ssh',20), SimpleNamespace(stdout='proc4 2 0 0')])
        c=Collector('192.0.2.10',runner=runner,clock=lambda:123,monotonic=lambda:5)
        self.assertNotIn('nas_nfs_v4_compound_total',c.metrics())
        c.poll_once();self.assertIn('nas_nfs_v4_compound_total 100',c.metrics())
        c.poll_once();self.assertIn('nas_nfs_v4_compound_total 100',c.metrics());self.assertIn('nas_nfs_collection_success 0',c.metrics())
        c.poll_once();self.assertIn('nas_nfs_v4_compound_total 0',c.metrics());self.assertIn('nas_nfs_collection_success 1',c.metrics())
        c.metrics();self.assertEqual(runner.call_count,3)
        self.assertEqual(runner.call_args.kwargs,dict(capture_output=True,text=True,timeout=20,check=True))
        self.assertEqual(POLL_INTERVAL,300)

    def test_ssh_boundary(self):
        args=ssh_args('192.0.2.10')
        self.assertEqual(args[-2:],['admin@192.0.2.10','cat /proc/net/rpc/nfsd'])
        self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('IdentityAgent=none',args)
        for host in ['8.8.8.8','127.0.0.1','169.254.1.1','239.1.1.1','0.0.0.0','240.0.0.1','192.0.2.10;id']:
            with self.assertRaises(ValueError):ssh_args(host)
