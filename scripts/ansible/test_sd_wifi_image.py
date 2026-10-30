#!/usr/bin/env python3
"""Executable contracts for the isolated Pi4 SD image, without live systems."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
FILES=ROOT/'ansible/openwrt/roles/rpi_sd_wifi_image/files'
sys.path.insert(0,str(FILES))
import boot
import build_image
import wifi_config


class ConfigTests(unittest.TestCase):
    def test_literal_encoding_and_boundaries(self):
        ssid=' 日本語#= '
        secret=' #="$(id)\\ '
        data=('country=JP\nssid='+ssid+'\npassword='+secret+'\n').encode()
        parsed=wifi_config.parse_wifi_config(b'\xef\xbb\xbf'+data.replace(b'\n',b'\r\n'))
        self.assertEqual(parsed['ssid'],ssid)
        self.assertEqual(parsed['password'],secret)
        output=wifi_config.supplicant_config(parsed)
        self.assertNotIn(secret,output)
        self.assertIn('ssid='+ssid.encode().hex(),output)

    def test_psk_golden_vector(self):
        config={'country':'US','ssid':'IEEE','password':'password'}
        self.assertIn('psk=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e',wifi_config.supplicant_config(config))

    def test_invalid_input_never_echoed(self):
        baseline=b'country=JP\nssid=example\npassword=private-fixture-password\n'
        variants=[baseline+b'password=duplicate',baseline+b'unknown=secret-value',
                  baseline.replace(b'example',b''),baseline.replace(b'JP',b'ZZ'),
                  baseline.replace(b'example',b'x'*33),baseline.replace(b'example',b'x\x00y'),
                  baseline.replace(b'example',b'x\ry'),baseline.replace(b'example',b'x\x7fy'),
                  baseline.replace(b'example',b'\xff'),baseline.replace(b'example','x\u0085y'.encode()),
                  b'#'*4097,b'country=JP\nssid=example\n',baseline.replace(b'private-fixture-password',b'p'*64)]
        for data in variants:
            with self.subTest(length=len(data)):
                with self.assertRaises(wifi_config.ConfigError) as error:
                    wifi_config.parse_wifi_config(data)
                self.assertNotIn('private-fixture-password',str(error.exception))
                self.assertNotIn('secret-value',str(error.exception))

    def test_cli_is_secret_free_and_writes_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'wifi.conf').write_text('country=JP\nssid=example\npassword=private-fixture-password\n')
            result=subprocess.run([sys.executable,str(FILES/'wifi_config.py'),str(p/'wifi.conf'),'--supplicant-output',str(p/'wpa.conf')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout),{'ok':True})
            self.assertNotIn('private-fixture-password',result.stdout+result.stderr)
            self.assertEqual((p/'wpa.conf').stat().st_mode&0o777,0o600)


def lease(root='192.0.2.10:/gentoo/fixed-rpi4/nfs',role='overlay_id=fixture,roles=base,k3s_stg_agent,stage=stg'):
    return {'options':[{'tag':17,'data':list(root.encode())},{'tag':224,'data':list(role.encode())}]}


class LeaseTests(unittest.TestCase):
    def test_raw_networkctl_schema_and_role_list(self):
        selected=boot.select_root(lease())
        self.assertEqual(selected['roles'],'base,k3s_stg_agent')
        self.assertEqual(selected['path'],'/gentoo/fixed-rpi4/nfs')

    def test_base64_schema(self):
        value=lease()
        for option in value['options']:
            option['data']=base64.b64encode(bytes(option['data'])).decode()
        self.assertEqual(boot.select_root(value)['stage'],'stg')

    def test_networkctl_hex_schema(self):
        value=lease()
        for option in value['options']:
            option['data']=bytes(option['data']).hex()
        self.assertEqual(boot.select_root(value)['roles'],'base,k3s_stg_agent')

    def test_fail_closed_on_missing_duplicate_bad_or_production(self):
        variants=[{'options':[]},{'Options':[]},lease(root='192.0.2.10:/gentoo/../nfs'),
                  lease(root='192.0.2.10:/gentoo/latest/nfs'),lease(root='$(id):/nfs'),
                  lease(role='overlay_id=fixture,role=desktop,stage=prod'),
                  lease(role='overlay_id=fixture,role=a,roles=b,c,stage=stg'),
                  lease(role='overlay_id=fixture,roles=a,stage=stg,command=id')]
        duplicated=lease();duplicated['options'].append(duplicated['options'][0]);variants.append(duplicated)
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(boot.BootError):
                    boot.select_root(value)


class BootContracts(unittest.TestCase):
    def test_kernel_bundle_mismatch_is_rejected(self):
        expected={'kernel_release':'fixture','kernel_sha256':'a'*64,'modules_sha256':'b'*64,'boot_protocol_version':1}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=root/'usr/lib/sd-netboot/kernel-bundle.json';p.parent.mkdir(parents=True)
            p.write_text(json.dumps(expected));(root/'usr/lib/modules/fixture').mkdir(parents=True)
            boot.verify_root(root,expected)
            p.write_text(json.dumps({**expected,'modules_sha256':'c'*64}))
            with self.assertRaisesRegex(boot.BootError,'kernel_bundle_mismatch'):
                boot.verify_root(root,expected)

    def test_diagnostics_rotates_and_stays_bounded(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(os,'sync'),contextlib.redirect_stdout(io.StringIO()):
            p=Path(directory)
            first=boot.Diagnostics(p,'fixture','11111111-1111-1111-1111-111111111111')
            first.event('config_valid')
            second=boot.Diagnostics(p,'fixture','22222222-2222-2222-2222-222222222222')
            for _ in range(280):
                second.event('wifi_associated')
            self.assertIn(first.boot_id,(p/'previous-boot-status.txt').read_text())
            self.assertLess(sum(x.stat().st_size for x in p.iterdir()),128*1024)
            self.assertIn(second.boot_id,(p/'boot-status.txt').read_text())
            with self.assertRaises(boot.BootError):
                second.event('wifi_associated',reason='arbitrary_password')

    def test_diagnostic_io_failure_is_not_boot_success(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            p=Path(directory)/'not-directory';p.touch()
            d=boot.Diagnostics(p,'fixture','11111111-1111-1111-1111-111111111111')
            d.event('root_selected','fail','root_or_role_missing')
            self.assertTrue(d.io_failed)

    def test_daemons_have_ram_lifetime_flags_and_shared_units(self):
        for name in ['sd-netboot-wpa.service','sd-netboot-networkd.service']:
            value=(FILES/'units'/name).read_text()
            for line in ['DefaultDependencies=no','IgnoreOnIsolate=yes','RefuseManualStop=yes','SurviveFinalKillSignal=yes','RootDirectory=/run/sd-netboot-runtime']:
                self.assertIn(line,value)
        self.assertIn('systemd-networkd.service', (FILES/'boot.py').read_text())
        self.assertIn(' - nfs(4)? ',(FILES/'shutdown-network.sh').read_text())


class ArtifactTests(unittest.TestCase):
    def test_vendor_plus_kernel_reaches_readonly_source_guard(self):
        argv=['builder','--sysroot','/source','--tftp-dir','/assets','--work-dir','/work/new',
              '--output-dir','/output/new','--image-id','fixture','--kernel-release','6.18.56-v8+',
              '--source-revision','a'*40]
        with patch.object(sys,'argv',argv),patch.object(build_image,'source_is_readonly',side_effect=RuntimeError('readonly_boundary_reached')):
            with self.assertRaisesRegex(RuntimeError,'readonly_boundary_reached'):
                build_image.main()

    def test_mbr_is_bootable_and_partuuid_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'test.img';build_image.mbr(image,256,0x12345678)
            with image.open('rb') as f: value=f.read(512)
            self.assertEqual(value[510:],b'\x55\xaa')
            self.assertEqual(struct.unpack_from('<I',value,440)[0],0x12345678)
            entry=struct.unpack('<B3sB3sII',value[446:462])
            self.assertEqual((entry[0],entry[2],entry[4],entry[5]),(0x80,0x0c,2048,256*2048-2048))
            with self.assertRaises(FileExistsError):
                build_image.mbr(image,256,0x12345678)

    def test_fat_roundtrip_and_empty_credentials(self):
        if not shutil.which('mformat') or not shutil.which('mcopy'):
            self.skipTest('mtools integration runs on the ARM64 builder')
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);build_image.LOG=p/'build.log'
            image=p/'test.img';build_image.mbr(image,256,0x12345678)
            build_image.command(['mformat','-i',str(image)+'@@1048576','-F','-T',str(256*2048-2048),'-v','RPI_NETBOOT','::'])
            (p/'wifi.conf').write_bytes(build_image.EMPTY_WIFI)
            manifest={'files':{}}
            (p/'boot-manifest.json').write_text(json.dumps(manifest))
            for name in ['wifi.conf','boot-manifest.json']:
                build_image.command(['mcopy','-i',str(image)+'@@1048576',p/name,'::/'])
            build_image.verify_image(image,p,manifest)
            (p/'wifi.conf').write_text('password=must-not-be-distributed\n')
            build_image.command(['mcopy','-o','-i',str(image)+'@@1048576',p/'wifi.conf','::/'])
            with self.assertRaisesRegex(ValueError,'image_credentials_not_empty'):
                build_image.verify_image(image,p,manifest)


if __name__=='__main__':
    unittest.main()
