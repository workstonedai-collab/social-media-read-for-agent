import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location("bootstrap",ROOT/"bootstrap.py")
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)

class SetupTests(unittest.TestCase):
    def test_preserves_other_settings_and_idempotent(self):
        text='model = "keep"\n[mcp_servers.other]\ncommand = "preserve"\n[mcp_servers.social-media-read-for-agent]\ncommand = "old"\n[mcp_servers.social-media-read-for-agent.env]\nOLD = "yes"\n[features]\nother = true\n'
        new=bootstrap.merge_codex(text,'/python with space')
        self.assertIn('[mcp_servers.other]\ncommand = "preserve"',new)
        self.assertIn('[features]\nother = true',new)
        self.assertNotIn('OLD',new);self.assertEqual(new.count('[mcp_servers.social-media-read-for-agent]'),1)
        self.assertEqual(bootstrap.merge_codex(new,'/python with space'),new)
        try:import tomllib
        except ImportError:return
        self.assertEqual(tomllib.loads(new)['mcp_servers']['social-media-read-for-agent']['command'],'/python with space')

    def test_keeps_other_quoted_server_with_dot_in_its_name(self):
        text='[mcp_servers."social-media-read-for-agent.backup"]\ncommand = "keep"\n[mcp_servers."social-media-read-for-agent"]\ncommand = "old"\n[[hooks.Stop]]\ncommand = "other"\n'
        new=bootstrap.merge_codex(text,'/python')
        self.assertIn('[mcp_servers."social-media-read-for-agent.backup"]\ncommand = "keep"',new)
        self.assertIn('[[hooks.Stop]]\ncommand = "other"',new)
        self.assertNotIn('command = "old"',new)

    def test_never_adopts_unowned_default_profile(self):
        self.assertIsNone(bootstrap.discover_profile(['daily'],None))
        self.assertEqual(bootstrap.discover_profile(['daily','new'],None,['daily']),'new')
        self.assertIsNone(bootstrap.discover_profile(['a','b'],None,[]))
        self.assertEqual(bootstrap.discover_profile(['known'],'known'),'known')

    def test_config_backup_and_no_rewrite(self):
        old=bootstrap.STATE
        with tempfile.TemporaryDirectory() as td:
            bootstrap.STATE=Path(td)/'state';target=Path(td)/'config'
            try:
                target.write_text('old')
                self.assertTrue(bootstrap.private_write(target,'new'))
                self.assertFalse(bootstrap.private_write(target,'new'))
                backups=list((bootstrap.STATE/'setup-backups').iterdir())
                self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),'old')
                self.assertEqual(target.stat().st_mode & 0o777,0o600)
            finally:bootstrap.STATE=old

class FidelityTests(unittest.TestCase):
    def test_resolved_article_is_cached_under_original_search_ref(self):
        from social_read.storage import Store
        with tempfile.TemporaryDirectory() as td:
            store=Store(td)
            try:
                target='https://weixin.sogou.com/link?url=exact-source&type=2'
                found=store.save_item('weixin',{'_target':target,'query':'工具','title':'题目'})
                article=store.save_item('weixin',{'_target':'https://mp.weixin.qq.com/s?src=11&signature=private','title':'题目','body':'正文','search_source_url':target},read=True)
                store.bind_read(found['content_ref'],article)
                cached=store.item(found['content_ref'])
                self.assertEqual(cached['data']['body'],'正文');self.assertEqual(cached['data']['query'],'工具')
                self.assertEqual(cached['target'],target);self.assertIsNotNone(cached['read_at'])
            finally:store.close()

    def test_legacy_source_cache_requires_exact_link_not_title(self):
        from social_read.storage import Store
        with tempfile.TemporaryDirectory() as td:
            store=Store(td)
            try:
                a=store.save_item('weixin',{'_target':'https://weixin.sogou.com/link?url=a','title':'同题'})
                b=store.save_item('weixin',{'_target':'https://weixin.sogou.com/link?url=b','title':'同题'})
                store.save_item('weixin',{'_target':'https://mp.weixin.qq.com/s/test','title':'同题','body':'真正原文','search_source_url':'https://weixin.sogou.com/link?url=a'},read=True)
                self.assertEqual(store.item(a['content_ref'])['data']['body'],'真正原文')
                self.assertIsNone(store.item(b['content_ref'])['read_at'])
            finally:store.close()

    def test_article_age_uses_explicit_calendar_date(self):
        from social_read.model import publication_age
        from datetime import date
        self.assertEqual(publication_age('2019-8-18',date(2026,10,15))['elapsed_whole_years'],7)
        self.assertIsNone(publication_age('08-18',date(2026,10,15)))
        self.assertIsNone(publication_age('2027-01-01',date(2026,10,15)))

    def test_image_comment_placeholder_is_not_user_text(self):
        from social_read.comments import normalize_rows
        rows=normalize_rows([{'id':'1','text':'图片评论 http://t.cn/example'},{'id':'2','text':'这个图片评论功能不错'}],'weibo',2,0)
        self.assertEqual(rows[0]['content_kind'],'non_text')
        self.assertEqual(rows[1]['content_kind'],'text')

    def test_ocr_cache_scope_changes_with_backend(self):
        from social_read.images import LocalOCR
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            with patch.dict('os.environ',{'SOCIAL_READ_OCR':'vision'}):first=LocalOCR(td).cache_version
            with patch.dict('os.environ',{'SOCIAL_READ_OCR':'rapidocr'}):second=LocalOCR(td).cache_version
            self.assertNotEqual(first,second)

class GatewayTests(unittest.TestCase):
    def test_busy_task_defers_update_without_disconnecting(self):
        from social_read.gateway import Gateway
        from unittest.mock import Mock,patch
        gateway=Gateway(ROOT,[]);gateway.worker=Mock();gateway.worker.poll.return_value=None
        gateway.loaded_revision='old';gateway.ready=True;gateway.tasks={'task-active'}
        def exchange(worker,message):
            value={'status':'running','task_id':'task-active'} if message.get('params',{}).get('name')=='get_task' else {'version':'old'}
            return {'result':{'content':[{'type':'text','text':json.dumps(value)}]}}
        gateway.exchange=exchange;gateway.start=Mock()
        with patch('social_read.gateway.revision',return_value='new'):
            result=gateway.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'list_platforms','arguments':{}}})
        gateway.start.assert_not_called()
        self.assertTrue(json.loads(result['result']['content'][0]['text'])['runtime']['update_pending'])
        self.assertEqual(gateway.tasks,{'task-active'})


    def test_same_stdio_connection_loads_new_code_and_keeps_tasks(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);shutil.copytree(ROOT/'social_read',root/'social_read',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(ROOT/'start_mcp.py',root/'start_mcp.py')
            proc=subprocess.Popen([sys.executable,str(root/'start_mcp.py'),'--mode','demo','--state-dir',str(root/'state'),'mcp'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            counter=0
            def rpc(method,params=None,notification=False):
                nonlocal counter
                counter+=1;msg={'jsonrpc':'2.0','method':method,'params':params or {}}
                if not notification:msg['id']=counter
                proc.stdin.write(json.dumps(msg)+'\n');proc.stdin.flush()
                if not notification:return json.loads(proc.stdout.readline())
            def tool(name,args=None):
                out=rpc('tools/call',{'name':name,'arguments':args or {}})
                self.assertNotIn('error',out);return json.loads(out['result']['content'][0]['text'])
            try:
                rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
                rpc('notifications/initialized',notification=True)
                first=tool('list_platforms');pid=proc.pid
                task=tool('search',{'platforms':['weibo'],'query':'测试'})
                for _ in range(100):
                    job=tool('get_task',{'task_id':task['task_id']})
                    if job['status']=='success':break
                    time.sleep(.01)
                p=root/'social_read/__init__.py';p.write_text(p.read_text().replace('0.1.0a5','0.1.0a5.test'))
                second=tool('list_platforms')
                self.assertEqual(proc.pid,pid);self.assertEqual(second['version'],'0.1.0a5.test')
                self.assertNotEqual(first['runtime']['loaded_revision'],second['runtime']['loaded_revision'])
                self.assertEqual(second['runtime']['reloads'],1)
                self.assertEqual(tool('get_task',{'task_id':task['task_id']})['status'],'success')
                self.assertEqual(tool('read_content',{'content_ref':job['result']['items'][0]['content_ref']})['status'],'queued')
            finally:
                proc.stdin.close();proc.wait(timeout=10);proc.stdout.close()

if __name__=='__main__':unittest.main()
