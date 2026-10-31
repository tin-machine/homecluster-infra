#!/usr/bin/env python3
"""Execute the production preflight shell with isolated filesystem probes."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import jinja2
import yaml

ROOT=Path(__file__).resolve().parents[2]
loader=importlib.machinery.SourceFileLoader('capacity_precheck',str(ROOT/'scripts/pi-rpi5-common-kernel-precheck'))
spec=importlib.util.spec_from_loader(loader.name,loader)
precheck=importlib.util.module_from_spec(spec);loader.exec_module(precheck)


class BuilderCapacityTests(unittest.TestCase):
    def setUp(self):
        plays=yaml.safe_load((ROOT/'ansible/openwrt/playbooks/rpi5-common-kernel-precheck.yml').read_text())
        self.play=next(p for p in plays if p.get('hosts')=='homecluster_common_kernel_builder')
        self.task=next(t for t in self.play['tasks'] if t.get('name')=='Pi5 common kernel builder空き容量を確認')

    def test_default_includes_imagefs_reclaim_and_build_headroom(self):
        self.assertEqual(self.play['vars']['homecluster_common_kernel_builder_min_free_kib'],48*1024**2)

    def test_actual_shell_rejects_low_space_and_accepts_exact_boundary(self):
        required=48*1024**2
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bin').mkdir()
            for available in [8*1024**2,20*1024**2,required-1,required,required+1]:
                with self.subTest(available=available):
                    (root/'bin/df').write_text('#!/bin/sh\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted_on\\nfixture 239253116 1 '+str(available)+' 90%% /fixture\\n"\n')
                    (root/'bin/df').chmod(0o755)
                    values={**self.play['vars'],'rpi5_common_kernel_build_work_root':str(root/'not-yet-created/work')}
                    shell=jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(self.task['ansible.builtin.shell']).render(**values)
                    result=subprocess.run(['/bin/bash','-c',shell],capture_output=True,text=True,
                                          env={**os.environ,'PATH':str(root/'bin')+':'+os.environ['PATH']})
                    self.assertEqual(result.returncode,0 if available>=required else 1)
                    if available<required:
                        self.assertIn(f'Pi5 builder capacity gate failed: available={available}KiB required={required}KiB',result.stderr)

    def failure(self,available,required):
        output=f'fatal: secret=do-not-copy Pi5 builder capacity gate failed: available={available}KiB required={required}KiB\n'
        completed=subprocess.CompletedProcess(['ansible-playbook'],2,stdout=output,stderr='')
        stream=io.StringIO()
        with patch.object(precheck,'source_checks',return_value=[]),patch.object(precheck,'run',return_value=completed),contextlib.redirect_stdout(stream):
            self.assertEqual(precheck.main(['--json']),2)
        self.assertNotIn('do-not-copy',stream.getvalue())
        return json.loads(stream.getvalue())

    def test_builder_failure_exposes_only_bounded_numbers(self):
        result=self.failure(20*1024**2,48*1024**2)
        self.assertEqual(result['reason'],'builder_capacity_insufficient')
        self.assertEqual(result['diagnostics'],['builder_available_kib=20971520','builder_required_kib=50331648','builder_shortfall_kib=29360128'])

    def test_inconsistent_and_overflow_values_remain_generic(self):
        for available,required in [(120,100),(100,100),(0,2**63)]:
            result=self.failure(available,required)
            self.assertEqual(result['reason'],'common_kernel_runtime_playbook_failed')
            self.assertEqual(result['diagnostics'],[])


if __name__=='__main__':
    unittest.main()
