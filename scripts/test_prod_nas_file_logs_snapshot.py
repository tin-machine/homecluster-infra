import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        text = Path(__file__).with_name('nas_file_logs_snapshot.sh').read_text()
        self.paths = sorted(set(re.findall(r'"(/(?:mnt/HDA_ROOT/\.logs|usr/local/samba/var|var/log)/[^"\n]+)"', text)))
        for i, path in enumerate(self.paths):
            text = text.replace('"' + path + '"', '"' + str(self.root / str(i)) + '"')
        self.script = self.root / 'snapshot.sh'
        self.script.write_text(text)

    def run_helper(self, command='nas-logs-v1'):
        return subprocess.run(['sh', str(self.script)], input=b'', capture_output=True,
                              env={**os.environ, 'SSH_ORIGINAL_COMMAND': command}, timeout=3)

    def test_missing_and_command_denial(self):
        for command in ['', 'cat /etc/passwd', 'nas-logs-v1 extra']:
            r = self.run_helper(command)
            self.assertEqual(r.returncode, 64)
            self.assertEqual(r.stdout, b'')
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(b'NASLOG1\n'))
        self.assertTrue(r.stdout.endswith(b'END\n'))
        self.assertEqual(len(r.stdout.splitlines()), len(self.paths) + 2)

    def test_binary_length_precedes_payload(self):
        payloads = [b'', b'no-newline', '日本語\nline2\n'.encode(), b'F fake\x00\xffEND\n']
        for i in range(len(self.paths)):
            (self.root / str(i)).write_bytes(payloads[i % len(payloads)])
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        body = r.stdout.removeprefix(b'NASLOG1\n')
        observed = []
        while body != b'END\n':
            header, body = body.split(b'\n', 1)
            fields = header.split()
            self.assertEqual(len(fields), 7)
            self.assertEqual(fields[0], b'F')
            size = int(fields[5])
            observed.append(body[:size])
            body = body[size:]
        self.assertCountEqual(observed, [payloads[i % 4] for i in range(len(self.paths))])

    def test_oversize_is_not_a_complete_snapshot(self):
        (self.root / '0').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        r = self.run_helper()
        self.assertEqual(r.returncode, 65)
        self.assertFalse(r.stdout.endswith(b'END\n'))


if __name__ == '__main__':
    unittest.main()
