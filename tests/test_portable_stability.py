import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('live_stability',ROOT/'scripts/verify_live_stability.py')
stability=importlib.util.module_from_spec(spec);spec.loader.exec_module(stability)


class PortableTests(unittest.TestCase):
    def test_runner_finds_project_node_without_shell_profile(self):
        from social_read import runtime
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);node=root/'.state/runtime/node/bin';node.mkdir(parents=True)
            cli=root/'.state/opencli/node_modules/.bin';cli.mkdir(parents=True)
            executable=cli/'opencli';executable.write_text('#!/bin/sh\n');executable.chmod(0o700)
            with patch.object(runtime,'ROOT',root),patch.dict(os.environ,{'PATH':'/usr/bin:/bin'}):
                env=runtime.command_env();self.assertEqual(env['PATH'].split(os.pathsep)[:2],[str(node),str(cli)])
                self.assertEqual(runtime.opencli_executable(),str(executable))

    def test_bad_runtime_checksum_stops_before_execution_or_client_config(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);shutil.copy(ROOT/'install.sh',root/'install.sh')
            shim=root/'test commands';shim.mkdir()
            fake=shim/'curl';fake.write_text('#!/bin/bash\nwhile [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then shift; printf bad-archive > "$1"; exit 0; fi; shift; done\nexit 7\n');fake.chmod(0o700)
            osname='darwin' if os.uname().sysname=='Darwin' else 'linux'
            arch='arm64' if os.uname().machine in ('arm64','aarch64') else 'x64'
            (root/'runtime-lock.tsv').write_text('python-'+osname+'-'+arch+'\t3.11.17\thttps://github.com/astral-sh/python-build-standalone/releases/download/fake\t'+'0'*64+'\n')
            result=subprocess.run(['/bin/bash',str(root/'install.sh'),'--runtime-only'],env=dict(os.environ,PATH=str(shim)+':/usr/bin:/bin'),text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertIn('校验失败',result.stderr)
            self.assertFalse((root/'.state/runtime/python').exists());self.assertFalse((root/'.state/install.lock').exists())

    def test_locked_installer_does_not_remove_other_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);shutil.copy(ROOT/'install.sh',root/'install.sh');lock=root/'.state/install.lock';lock.mkdir(parents=True)
            result=subprocess.run(['/bin/bash',str(root/'install.sh'),'--runtime-only'],text=True,capture_output=True)
            self.assertEqual(result.returncode,3);self.assertTrue(lock.exists())


class StabilityTests(unittest.TestCase):
    def test_days_are_distinct_and_demo_never_counts_as_live(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for date,mode in [('2026-10-15','live'),('2026-10-16','live'),('2026-10-17','demo')]:
                stability.atomic_json(root/(date+'.json'),{'date':date,'mode':mode,'finished_at':'fixed','all_requested_reads_passed':date=='2026-10-16'})
            stability.atomic_json(root/'2026-10-15-repeat.json',{'date':'2026-10-15','mode':'live','finished_at':'fixed'})
            stability.atomic_json(root/'2026-10-18.json',{'date':'2026-10-18','mode':'live'})
            value=stability.summarize(root)
            self.assertEqual(value['observed_dates'],['2026-10-15','2026-10-16']);self.assertEqual(value['distinct_observed_days'],2)
            self.assertEqual(value['dates_all_requested_reads_passed'],['2026-10-16'])

    def test_access_denial_is_retained_and_no_comment_or_retry_requested(self):
        class Engine:
            mode='live'
            def __init__(self):self.calls=[]
            def submit(self,action,args):self.calls.append((action,args));return {'task_id':'job'}
            def wait(self,*args):return {'task_id':'job','status':'failed','result':{'errors':[{'status':'access_denied'}]}}
        engine=Engine();result=stability.sample(engine,['xiaohongshu'],'主题')
        self.assertEqual(len(engine.calls),1);self.assertFalse(result['all_requested_reads_passed'])
        self.assertEqual(result['results'][0]['search']['errors'][0]['status'],'access_denied')

    def test_refresh_read_then_bounded_comments_and_no_wechat_comments(self):
        class Engine:
            mode='live'
            def __init__(self):self.calls=[]
            def submit(self,action,args):self.calls.append((action,args));return {'task_id':action}
            def wait(self,ident,*args):
                result={'items':[{'content_ref':'source'}],'coverage':{'returned_by_platform':{'weixin':1,'xiaohongshu':1}}} if ident=='search' else {'content':{'body_chars':10,'cache':{'used':False}}} if ident=='read' else {'coverage':{'returned_rows':1}}
                return {'task_id':ident,'status':'success','result':result}
        engine=Engine();result=stability.sample(engine,['xiaohongshu','weixin'],'主题')
        self.assertTrue(result['all_requested_reads_passed']);self.assertTrue(result['all_requested_comments_passed'])
        reads=[args for name,args in engine.calls if name=='read'];self.assertTrue(all(r['refresh'] for r in reads))
        comments=[args for name,args in engine.calls if name=='comments'];self.assertEqual(len(comments),1);self.assertEqual(comments[0]['limit'],2)


if __name__=='__main__':unittest.main()
