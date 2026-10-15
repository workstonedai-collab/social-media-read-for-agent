import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from social_read.adapter import Runner, DemoAdapter
from social_read.engine import Engine
from social_read.images import enrich_images, LocalOCR
from social_read.model import content_item, ReadError
from social_read.storage import Store
from social_read.comments import normalize_rows, read_comments
from social_read.weixin_public import ArticleParser, article_item

XHS='https://www.xiaohongshu.com/explore/000000000000000000000001?xsec_token=PRIVATE'
WB='https://weibo.com/100000/AbCd'
IMAGE='https://sns-webpic.xhscdn.com/'
def note():return content_item('xiaohongshu',{'content':'配文'},XHS,'test')
def recognized(text='图中文字'):return {'status':'success','text':text,'text_chars':len(text)}

class RecoveryTests(unittest.TestCase):
    def test_only_missing_image_reprocessed_after_new_instance(self):
        with tempfile.TemporaryDirectory() as td:
            runner=Runner(td);event=threading.Event()
            with patch('social_read.images.LocalOCR.read',side_effect=[recognized('一'),ReadError('image_unavailable','未取得'),recognized('三')]):
                first=enrich_images(note(),[IMAGE+str(i) for i in range(3)],runner,event,'note_state_carousel',3)
            self.assertEqual(first['coverage']['unread_orders'],[2])
            with patch('social_read.images.LocalOCR.read',return_value=recognized('二')) as read:
                second=enrich_images(note(),[IMAGE+str(i) for i in range(3)],Runner(td),event,'note_state_carousel',3)
            self.assertEqual(read.call_count,1)
            self.assertEqual(read.call_args[0][0],IMAGE+'1')
            self.assertEqual(second['coverage']['images_reused'],2)
            self.assertTrue(second['coverage']['images_all_processed'])

    def test_platform_image_id_survives_rotating_cdn_url(self):
        with tempfile.TemporaryDirectory() as td, patch('social_read.images.LocalOCR.read',return_value=recognized()) as read:
            enrich_images(note(),[IMAGE+'signed-old'],Runner(td),threading.Event(),'note_state_carousel',1,image_ids=['asset-id'])
            value=enrich_images(note(),[IMAGE+'signed-new'],Runner(td),threading.Event(),'note_state_carousel',1,image_ids=['asset-id'])
            self.assertEqual(read.call_count,1);self.assertEqual(value['coverage']['images_reused'],1)

    def test_cancel_keeps_checkpoint_and_progress_then_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            event=threading.Event();updates=[]
            def progress(item):
                updates.append(json.loads(json.dumps(item)))
                if item['coverage']['ocr_succeeded']==1:event.set()
            with patch('social_read.images.LocalOCR.read',return_value=recognized()):
                with self.assertRaises(ReadError):enrich_images(note(),[IMAGE+'1',IMAGE+'2'],Runner(td),event,'note_state_carousel',2,progress=progress)
            self.assertEqual(updates[-1]['coverage']['ocr_succeeded'],1)
            with patch('social_read.images.LocalOCR.read',return_value=recognized()) as read:
                result=enrich_images(note(),[IMAGE+'1',IMAGE+'2'],Runner(td),threading.Event(),'note_state_carousel',2)
            self.assertEqual(read.call_count,1);self.assertEqual(result['coverage']['images_reused'],1)

    def test_changed_manifest_does_not_reuse_by_position(self):
        with tempfile.TemporaryDirectory() as td,patch('social_read.images.LocalOCR.read',return_value=recognized()) as read:
            enrich_images(note(),[IMAGE+'1'],Runner(td),threading.Event(),'note_state_carousel',1)
            enrich_images(note(),[IMAGE+'different'],Runner(td),threading.Event(),'note_state_carousel',1)
            self.assertEqual(read.call_count,2)

    def test_expired_checkpoint_and_access_stop(self):
        with tempfile.TemporaryDirectory() as td:
            with patch('social_read.images.LocalOCR.read',side_effect=ReadError('image_rate_limited','停止')) as read:
                value=enrich_images(note(),[IMAGE+'1',IMAGE+'2'],Runner(td),threading.Event(),'note_state_carousel',2)
            self.assertEqual(read.call_count,1);self.assertEqual(value['image_ocr'][1]['status'],'not_read_access_stop')

    def test_budget_reaches_download(self):
        with tempfile.TemporaryDirectory() as td:
            ocr=LocalOCR(td);deadline=time.monotonic()+10
            def download(url,path,cancel,stop):
                self.assertEqual(stop,deadline);Path(path).write_bytes(b'pixels')
            with patch.object(ocr,'command',return_value=['/ocr']),patch('social_read.images.download_image',side_effect=download),patch('social_read.images.run_local',return_value=json.dumps({'status':'success','lines':[]})):
                ocr.read(IMAGE+'1',threading.Event(),deadline)

    def test_xhs_partial_cache_requests_recovery(self):
        class Partial(DemoAdapter):
            calls=0
            def read(self,*args):
                self.calls+=1;item=super().read(*args)
                item['image_ocr']=[];item['coverage']={'ocr_unread':1 if self.calls==1 else 0}
                return item
        with tempfile.TemporaryDirectory() as td:
            adapter=Partial();engine=Engine(td,'demo',adapter=adapter)
            try:
                first=engine.wait(engine.submit('read',{'platform':'xiaohongshu','url':XHS})['task_id'],3)
                second=engine.wait(engine.submit('read',{'content_ref':first['result']['content']['content_ref']})['task_id'],3)
                self.assertEqual(first['status'],'partial');self.assertEqual(second['status'],'success');self.assertEqual(adapter.calls,2)
            finally:engine.shutdown()

class CursorTests(unittest.TestCase):
    def test_cursor_bound_to_article_profile_and_expiry(self):
        with tempfile.TemporaryDirectory() as td:
            a=Store(Path(td)/'a');b=Store(Path(td)/'b')
            try:
                token=a.save_comment_cursor('weibo',WB,{'offset':'1999999999999999999'})
                self.assertEqual(a.comment_cursor(token,'weibo',WB)['offset'],'1999999999999999999')
                for store,url in [(a,WB+'2'),(b,WB)]:
                    with self.assertRaises(ReadError):store.comment_cursor(token,'weibo',url)
                with patch('social_read.storage.time.time',return_value=time.time()+1801):
                    with self.assertRaises(ReadError):a.comment_cursor(token,'weibo',WB)
            finally:a.close();b.close()

    def test_duplicate_roots_and_replies_do_not_consume_limit(self):
        rows=[{'id':'1','text':'根'}, {'id':'1','text':'根'}, {'id':'2','text':'回复','parent_id':'1'}, {'id':'2','text':'回复','parent_id':'1'}, {'id':'3','text':'回复二','parent_id':'1'}, {'id':'4','text':'根二'}]
        got=normalize_rows(rows,'weibo',2,2)
        self.assertEqual([x['id'] for x in got],['1','2','3','4'])

    def test_orphan_reply_not_attached_to_unknown_root(self):
        self.assertEqual(normalize_rows([{'id':'2','parent_id':'unknown','text':'回复'}],'weibo',2,2),[])

    def test_engine_cursor_is_opaque_and_can_continue(self):
        class Adapter(DemoAdapter):
            def comments(self,site,target,limit,cancel,replies_limit=3,cursor=None):
                return {'items':[{'id':'second' if cursor else 'first','text':'真实结构的离线样例'}], 'coverage':{'all_comments':False}, '_continuation':None if cursor else {'offset':'next'}}
        with tempfile.TemporaryDirectory() as td:
            engine=Engine(td,'demo',adapter=Adapter())
            try:
                first=engine.wait(engine.submit('comments',{'platform':'weibo','url':WB})['task_id'],3)
                token=first['result']['next_cursor'];self.assertTrue(token.startswith('comments_'))
                self.assertNotIn('_continuation',first['result'])
                second=engine.wait(engine.submit('comments',{'platform':'weibo','url':WB,'cursor':token})['task_id'],3)
                self.assertTrue(second['result']['coverage']['continued']);self.assertIsNone(second['result']['next_cursor'])
                self.assertEqual(second['result']['items'][0]['id'],'second')
            finally:engine.shutdown()

class ArticleFidelityTests(unittest.TestCase):
    def test_code_blocks_math_lazy_images_and_date(self):
        raw='''<h2 id="activity-name">代码与数学</h2><a id="js_name">作者</a><div id="js_content"><p>如果 a &lt; b 且 c &gt; d</p><pre data-lang="python"><span class="code-snippet__line-index">1</span><code>if a &lt; b:</code><code>    run()</code></pre><img data-src="//mmbiz.qpic.cn/a"><section style="display:none">隐藏广告</section></div><script>var ct = "1690000000";</script>'''
        parser=ArticleParser();parser.feed(raw);article=parser.article(raw)
        item=article_item(article,'https://mp.weixin.qq.com/s/example','test',True)
        self.assertIn('a < b',item['body']);self.assertIn('```python\nif a < b:\n    run()',item['body'])
        self.assertNotIn('隐藏广告',item['body']);self.assertEqual(item['coverage']['code_block_count'],1)
        self.assertEqual(item['published_at_source'],'page_script_timestamp');self.assertTrue(item['published_at_raw'].startswith('2023-'))
        self.assertTrue(item['coverage']['body_closed']);self.assertEqual(item['media'][0]['url'],'https://mmbiz.qpic.cn/a')

    def test_unclosed_or_unstable_body_marked_partial(self):
        raw='<h2 id="activity-name">标题</h2><div id="js_content"><p>正文</p>'
        parser=ArticleParser();parser.feed(raw)
        item=article_item(parser.article(raw),'https://mp.weixin.qq.com/s/example','test',False)
        self.assertEqual(item['read_errors'][0]['status'],'content_unstable')

    def test_article_mentioning_challenge_is_not_a_challenge_page(self):
        raw='<h2 id="activity-name">登录验证的设计</h2><div id="js_content">环境异常时去验证，是常见提示。</div>'
        parser=ArticleParser();parser.feed(raw);self.assertIn('环境异常',parser.article(raw)['content'])


class CommentBrowserScriptTests(unittest.TestCase):
    def test_weibo_api_overfull_page_retains_buffer_without_skipping_roots(self):
        import shutil,subprocess
        node=shutil.which('node')
        if not node:self.skipTest('Node is required for fixed browser script test')
        class Page:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def evaluate(self,script,*a):
                harness="""const timer=setTimeout;global.setTimeout=(f,ms)=>timer(f,0);
                global.location={origin:'https://weibo.com',href:'https://weibo.com/100000/AbCd'};
                global.fetch=async path=>({ok:true,text:async()=>JSON.stringify(path.includes('/show?')?{idstr:'1999999999999999999'}:{data:[1,2,3].map(i=>({id:'199999999999999999'+i,text_raw:'comment '+i})),max_id:0})});
                Promise.resolve(SCRIPT).then(v=>console.log(JSON.stringify(v))).catch(e=>{console.error(e);process.exit(1)});""".replace('SCRIPT',script)
                done=subprocess.run([node,'-e',harness],capture_output=True,text=True,timeout=5)
                if done.returncode:raise AssertionError(done.stderr)
                return json.loads(done.stdout)
        with patch('social_read.comments.ReadPage',Page):
            first=read_comments(None,'weibo',WB,1,0,threading.Event())
            second=read_comments(None,'weibo',WB,1,0,threading.Event(),first['_continuation'])
            third=read_comments(None,'weibo',WB,1,0,threading.Event(),second['_continuation'])
        self.assertEqual([r['items'][0]['id'] for r in [first,second,third]],['1999999999999999991','1999999999999999992','1999999999999999993'])
        self.assertIsNone(third['_continuation']);self.assertTrue(third['coverage']['root_end_reached'])


class SearchReadinessTests(unittest.TestCase):
    def page(self, href, text, empty=False):
        import shutil,subprocess
        node=shutil.which('node')
        if not node:self.skipTest('Node is required for fixed script check')
        class Page:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def wait(self,*a):pass
            def command(self,*a):pass
            tab="owned"
            def evaluate(self,script,*a):
                setup="""global.location={origin:'https://www.xiaohongshu.com',href:'https://www.zhihu.com/search?q=test'};
                const section={querySelector:()=>null};
                const link={textContent:TEXT,innerText:TEXT,getAttribute:()=>HREF,closest:()=>section};
                global.document={body:{innerText:EMPTY?'没有找到相关内容':'正常结果'},querySelector:()=>null,querySelectorAll:()=>EMPTY?[]:[link]};
                if(EMPTY){let tick=0;Date.now=()=>tick+=16000;}
                Promise.resolve(SCRIPT).then(v=>console.log(JSON.stringify(v))).catch(e=>{console.error(e);process.exit(1)});"""
                harness=setup.replace('TEXT',json.dumps(text)).replace('HREF',json.dumps(href)).replace('EMPTY',str(empty).lower()).replace('SCRIPT',script)
                result=subprocess.run([node,'-e',harness],capture_output=True,text=True,timeout=4)
                if result.returncode:raise AssertionError(result.stderr)
                return json.loads(result.stdout)
        return Page

    def test_xhs_title_link_fallback_without_old_css_class(self):
        from social_read.xiaohongshu_search import search_notes
        with patch('social_read.xiaohongshu_search.ReadPage',self.page(XHS,'真实标题')):
            rows=search_notes(None,'主题',1,threading.Event())
        self.assertEqual(rows[0]['title'],'真实标题')

    def test_xhs_explicit_empty_is_different_from_extraction_failure(self):
        from social_read.xiaohongshu_search import search_notes
        with patch('social_read.xiaohongshu_search.ReadPage',self.page(XHS,'',True)):
            with self.assertRaises(ReadError) as exc:search_notes(None,'主题',1,threading.Event())
        self.assertEqual(exc.exception.code,'search_empty_confirmed')

    def test_search_recovery_uses_new_owned_page_and_reports_first_failure(self):
        from social_read.xiaohongshu_search import search_notes
        from unittest.mock import MagicMock
        first=MagicMock();first.__enter__.return_value=first
        first.evaluate.side_effect=ReadError('content_loading_timeout','加载超时')
        second=MagicMock();second.__enter__.return_value=second
        second.evaluate.return_value={'items':[{'title':'可读笔记','url':XHS}]}
        with patch('social_read.xiaohongshu_search.ReadPage',side_effect=[first,second]) as pages,patch('social_read.xiaohongshu_search._pause'):
            result=search_notes(None,'主题',1,threading.Event())
        self.assertEqual(pages.call_count,2);first.__exit__.assert_called_once();second.__exit__.assert_called_once()
        self.assertEqual(result[0]['recovery']['previous_status'],'content_loading_timeout')
        self.assertEqual(result[0]['recovery']['attempts'],2)

    def test_search_never_recovers_verification_rate_limit_or_unknown_adapter_failure(self):
        from social_read.xiaohongshu_search import search_notes
        from unittest.mock import MagicMock
        for code in ['verification_required','rate_limited','access_denied','adapter_error']:
            page=MagicMock();page.__enter__.return_value=page;page.evaluate.side_effect=ReadError(code,'停止')
            with patch('social_read.xiaohongshu_search.ReadPage',return_value=page) as pages,patch('social_read.xiaohongshu_search._pause') as pause:
                with self.assertRaises(ReadError):search_notes(None,'主题',1,threading.Event())
            self.assertEqual(pages.call_count,1);pause.assert_not_called();page.__exit__.assert_called_once()

    def test_zhihu_normal_page_preserves_large_answer_id(self):
        from social_read.zhihu_search import search_zhihu
        url='https://www.zhihu.com/question/123/answer/2031299531585873699'
        with patch('social_read.zhihu_search.ReadPage',self.page(url,'答案标题')):
            rows=search_zhihu(None,'主题',1,threading.Event())
        self.assertEqual(rows[0]['_target'],url)

    def test_confirmed_empty_search_is_successful_zero_with_reason(self):
        class Empty(DemoAdapter):
            def search(self,*args):raise ReadError('search_empty_confirmed','平台明确显示零结果')
        with tempfile.TemporaryDirectory() as td:
            engine=Engine(td,'demo',adapter=Empty())
            try:
                job=engine.wait(engine.submit('search',{'platforms':['xiaohongshu'],'query':'主题'})['task_id'],3)
                self.assertEqual(job['status'],'success')
                self.assertEqual(job['result']['platform_outcomes']['xiaohongshu'],'confirmed_empty')
                self.assertEqual(job['result']['coverage']['returned_by_platform']['xiaohongshu'],0)
            finally:engine.shutdown()


class ZhihuVisibleFallbackTests(unittest.TestCase):
    def test_denied_api_is_not_retried_and_visible_body_is_preserved(self):
        from social_read.platform_reads import read_zhihu_answer
        calls=[]
        class Page:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def check(self):pass
            def evaluate(self,script,*a):
                calls.append(script)
                if len(calls)==1:return {'failure':'access_denied'}
                return {'title':'问题','content':'<p>已登录页面的可见回答</p>','visible_fallback':True,'dom_stable':True,'collapsed':False,'paywall':False,'created_at':'编辑于 2026-04-25'}
        with patch('social_read.platform_reads.ReadPage',Page):
            item=read_zhihu_answer(None,'https://www.zhihu.com/question/123/answer/456',threading.Event())
        self.assertEqual(len(calls),2);self.assertNotIn('fetch(',calls[1])
        self.assertEqual(item['body'],'已登录页面的可见回答')
        self.assertEqual(item['coverage']['api_status'],'access_denied')
        self.assertEqual(item['completeness'],'visible_body')


class InterruptedStateTests(unittest.TestCase):
    def test_dead_process_recovery_keeps_partial_content_and_progress(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(td);job=store.create_job('read',{'platform':'xiaohongshu','url':XHS})
            store.update_job(job,'running',{'content':{'content_ref':'content_partial','body':'配文和一张图'},'progress':{'completed':1,'total':9}})
            store.db.execute('update jobs set owner_pid=? where id=?',(999999999,job));store.db.commit();store.close()
            recovered=Store(td)
            try:
                row=recovered.job(job);self.assertEqual(row['status'],'interrupted')
                self.assertEqual(row['result']['content']['body'],'配文和一张图')
                self.assertEqual(row['result']['progress']['completed'],1)
            finally:recovered.close()

    def test_weibo_pagination_cursor_is_not_rounded_by_javascript(self):
        import shutil,subprocess
        from social_read.platform_reads import FETCH_JS
        node=shutil.which('node')
        if not node:self.skipTest('Node runtime required')
        harness="global.fetch=async()=>({ok:true,text:async()=>'{\"max_id\":1999999999999999999,\"id\":1999999999999999998}'});(async()=>{"+FETCH_JS+"console.log(JSON.stringify(await get('/fixed-path')));})()"
        proc=subprocess.run([node,'-e',harness],capture_output=True,text=True,timeout=4)
        self.assertEqual(proc.returncode,0,proc.stderr)
        value=json.loads(proc.stdout);self.assertEqual(value['max_id'],'1999999999999999999')
