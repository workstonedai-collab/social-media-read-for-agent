import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest
from unittest.mock import Mock, patch

from social_read.install_support import download_env, install_command, SetupError


class DownloadTests(unittest.TestCase):
    def runtime_fixture(self, root, deny=False):
        source=Path(__file__).resolve().parent.parent
        shutil.copyfile(source/'install.sh',root/'install.sh')
        commands=root/'commands';commands.mkdir()
        checksum=hashlib.sha256(b'part-complete').hexdigest()
        host='darwin' if sys.platform=='darwin' else 'linux'
        arch='arm64' if os.uname().machine in ('arm64','aarch64') else 'x64'
        (root/'runtime-lock.tsv').write_text(f'python-{host}-{arch}\t3.11.17\thttps://github.com/astral-sh/python-build-standalone/releases/download/fake\t{checksum}\n')
        script='''import sys,json
from pathlib import Path
a=sys.argv[1:]; p=Path(a[a.index('-o')+1]); log=p.parent/'calls.jsonl'
with log.open('a') as f:f.write(json.dumps(a)+'\\n')
if DENY:sys.exit(22)
if p.exists():
 assert p.read_bytes()==b'part';assert a[a.index('--continue-at')+1]=='-'
 p.write_bytes(b'part-complete');sys.exit(0)
p.write_bytes(b'part');sys.exit(18)
'''.replace('DENY',repr(deny))
        curl=commands/'curl';curl.write_text('#!'+sys.executable+'\n'+script);curl.chmod(0o700)
        tar=commands/'tar';tar.write_text('#!/bin/sh\nexit 9\n');tar.chmod(0o700)
        return commands,checksum

    def test_runtime_connection_interrupt_resumes_same_partial_before_checksum(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);commands,checksum=self.runtime_fixture(root)
            env=dict(os.environ,PATH=str(commands)+os.pathsep+os.environ['PATH'])
            p=subprocess.run(['/bin/bash',str(root/'install.sh'),'--runtime-only'],env=env,capture_output=True,text=True)
            self.assertEqual(p.returncode,9)  # deliberate tar stop after valid checksum
            cache=root/'.state/download-cache/runtimes'
            self.assertEqual((cache/(checksum+'.tar.gz')).read_bytes(),b'part-complete')
            self.assertEqual(len((cache/'calls.jsonl').read_text().splitlines()),2)
            self.assertFalse((root/'.state/install.lock').exists())

    def test_runtime_http_denial_stops_without_direct_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);commands,_=self.runtime_fixture(root,deny=True)
            env=dict(os.environ,PATH=str(commands)+os.pathsep+os.environ['PATH'])
            p=subprocess.run(['/bin/bash',str(root/'install.sh'),'--runtime-only'],env=env,capture_output=True,text=True)
            self.assertEqual(p.returncode,22)
            self.assertEqual(len((root/'.state/download-cache/runtimes/calls.jsonl').read_text().splitlines()),1)
            self.assertEqual(json.loads(p.stdout)['stage'],'python_download')

    def test_bad_proxy_uses_verified_direct_only_for_download_child(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'HTTPS_PROXY':'http://private:secret@localhost:9'}):
            with patch('social_read.install_support.subprocess.run', side_effect=[Mock(returncode=35), Mock(returncode=0)]) as calls:
                env=download_env('opencli','https://registry.npmjs.org/test',Path(td))
            self.assertNotIn('HTTPS_PROXY',env)
            self.assertIn('HTTPS_PROXY',os.environ)
            self.assertEqual(env['npm_config_noproxy'],'*')
            self.assertTrue(all('--insecure' not in c.args[0] for c in calls.call_args_list))
            self.assertNotIn('secret',(Path(td)/'download-routes.jsonl').read_text())

    def test_http_denial_and_certificate_failure_do_not_trigger_route_switch(self):
        for code in [22,60]:
            with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'HTTPS_PROXY':'http://localhost:9'}):
                with patch('social_read.install_support.subprocess.run',return_value=Mock(returncode=code)) as calls:
                    env=download_env('opencli','https://registry.npmjs.org/test',Path(td))
                self.assertEqual(calls.call_count,1)
                self.assertEqual(env['HTTPS_PROXY'],os.environ['HTTPS_PROXY'])

    def test_failed_download_preserves_stage_and_private_log_without_command_in_reason(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SetupError) as caught:
                install_command([sys.executable,'-c','import sys; print("ECONNRESET"); sys.exit(1)'],Path(td),'opencli_dependencies',5)
            detail=caught.exception.result()
            self.assertEqual(detail['stage'],'opencli_dependencies')
            self.assertEqual(detail['reason'],'dependency_network_unavailable')
            self.assertEqual(Path(detail['log_file']).stat().st_mode & 0o777,0o600)
            self.assertNotIn('python',detail['reason'])

    def test_timeout_reaps_install_process_and_preserves_actionable_failure(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SetupError) as caught:
                install_command([sys.executable,'-c','import time; time.sleep(30)'],Path(td),'ocr_dependencies',.1)
            self.assertEqual(caught.exception.result()['reason'],'dependency_download_timeout')

    def test_npm_lock_pins_all_remote_packages_with_integrity(self):
        root=Path(__file__).resolve().parent.parent/'dependencies/opencli'
        manifest=json.loads((root/'package.json').read_text());lock=json.loads((root/'package-lock.json').read_text())
        self.assertEqual(manifest['dependencies']['@jackwener/opencli'],'1.8.6')
        self.assertEqual(lock['packages']['']['dependencies'],manifest['dependencies'])
        for name,package in lock['packages'].items():
            if name:
                self.assertTrue(package['resolved'].startswith('https://registry.npmjs.org/'))
                self.assertTrue(package['integrity'].startswith('sha512-'))
