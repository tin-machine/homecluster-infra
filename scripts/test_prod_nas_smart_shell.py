"""Execute the real shell protocol with only its hardware commands substituted."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from nas_smart import parse_snapshot


class ShellTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('openssl'):
            self.skipTest('openssl unavailable')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        getcfg = self.root / 'getcfg'
        getcfg.write_text('''#!/bin/sh
bay=${1#PhysicalDisk_}
case "$2" in
 port_id) if [ "$MODE" = port ]; then echo 9; else echo "$bay"; fi ;;
 serial_no) [ "$MODE" != missing ] || exit 0
   if [ "$MODE" = swap ] && [ -f "$FIXTURE/read-$bay" ]; then echo replaced; else echo "serial-$bay"; fi ;;
 wwn) echo "wwn-$bay" ;;
 model) echo model ;;
 *) exit 65 ;;
esac
''')
        getcfg.chmod(0o700)
        reader = self.root / 'smart'
        reader.write_text('''#!/bin/sh
[ "$#" = 2 ] && [ "$1" = -d ] || exit 64
[ "$MODE" != failure ] || exit 65
touch "$FIXTURE/read-$2"
for attr in 005 010 184 187 188 191 194 197 198 199; do
 raw=0
 [ "$attr" != 194 ] || raw=39
 if [ "$2" = 2 ] && [ "$attr" = 188 ]; then raw=1; fi
 printf '%s Attribute %s 100 099 000 OK\\n' "$attr" "$raw"
done
''')
        reader.chmod(0o700)
        source = Path(__file__).with_name('nas_smart_snapshot.sh').read_text()
        self.assertIn('/sbin/get_hd_smartinfo -d "$bay"', source)
        self.assertNotIn('get_hd_smartinfo -i', source)
        source = source.replace('/sbin/getcfg', str(getcfg)).replace('/sbin/get_hd_smartinfo', str(reader))
        self.helper = self.root / 'nas_smart_snapshot.sh'; self.helper.write_text(source)
        entry = Path(__file__).with_name('nas_file_logs_snapshot.sh').read_text()
        self.entry = self.root / 'entry.sh'; self.entry.write_text(entry)

    def run_helper(self, mode='', command='nas-smart-v1'):
        env = {**os.environ, 'MODE': mode, 'FIXTURE': str(self.root), 'SSH_ORIGINAL_COMMAND': command}
        return subprocess.run(['/bin/sh', str(self.entry)], env=env, capture_output=True, timeout=10)

    def test_valid_protocol_and_no_raw_identity(self):
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(parse_snapshot(r.stdout)['2']['raw']['188'], 1)
        self.assertNotIn(b'serial-', r.stdout)
        self.assertNotIn(b'wwn-', r.stdout)
        self.assertEqual(len(list(self.root.glob('read-*'))), 4)

    def test_arbitrary_commands_cannot_read_smart(self):
        for command in ('', 'id', 'nas-smart-v1; id', 'nas-smart-v1 extra', 'nas-smart-v2'):
            with self.subTest(command=command):
                r = self.run_helper(command=command)
                self.assertEqual(r.returncode, 64)
                self.assertFalse(r.stdout)
        self.assertFalse(list(self.root.glob('read-*')))

    def test_missing_identity_wrong_port_failed_read_and_mid_read_swap(self):
        for mode in ('missing', 'port', 'failure', 'swap'):
            with self.subTest(mode=mode):
                r = self.run_helper(mode)
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(r.stdout.endswith(b'\nEND\n'))
                with self.assertRaises(ValueError): parse_snapshot(r.stdout)


if __name__ == '__main__':
    unittest.main()
