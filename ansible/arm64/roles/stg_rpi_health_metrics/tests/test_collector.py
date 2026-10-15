import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'files' / 'homecluster-rpi-health-metrics.py'
SPEC = importlib.util.spec_from_file_location('rpi_health_metrics', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CollectorTests(unittest.TestCase):
    def test_bit_mapping_and_iscsi_sessions(self):
        metrics = MODULE.render_metrics('throttled=0x50005\n', 1)
        self.assertIn('homecluster_iscsi_sessions 1\n', metrics)
        self.assertIn('condition="undervoltage",state="current"} 1', metrics)
        self.assertIn('condition="undervoltage",state="since_boot"} 1', metrics)
        self.assertIn('condition="throttled",state="current"} 1', metrics)
        self.assertIn('condition="throttled",state="since_boot"} 1', metrics)
        self.assertIn('condition="frequency_capped",state="current"} 0', metrics)

    def test_invalid_firmware_response_replaces_previous_sample_with_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'sample.prom'
            MODULE.write_atomic(output, MODULE.render_metrics('throttled=0x0', 0))
            with patch.object(MODULE.subprocess, 'run') as run:
                run.return_value.stdout = 'unexpected'
                self.assertFalse(MODULE.run(output=output))
            self.assertIn('homecluster_rpi_health_collection_success 0\n', output.read_text())
            self.assertNotIn('homecluster_iscsi_sessions', output.read_text())

    def test_iscsi_count_including_local_ssd_node(self):
        with tempfile.TemporaryDirectory() as directory:
            iscsi_class = Path(directory)
            (iscsi_class / 'session1').mkdir()
            (iscsi_class / 'session2').mkdir()
            with patch.object(MODULE.subprocess, 'run') as run:
                run.return_value.stdout = 'throttled=0x0\n'
                self.assertIn('homecluster_iscsi_sessions 2\n', MODULE.collect(iscsi_class=iscsi_class))
                run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
