import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from social_read.adapter import DemoAdapter
from social_read.comments import read_comments
from social_read.engine import Engine
from social_read.model import ReadError
from social_read.zhihu_comments import CURSOR_READER, read_visible_comments, snapshot

TARGET='https://www.zhihu.com/question/123/answer/456'

def entry(ident='1',children=None,expandable=False):
    return {'root':{'id':ident,'text':'评论','author':'甲'},'children':children or [],'expandable':expandable}

def snap(roots=None,**kwargs):
    return dict(ready=True,panel_open=True,roots=roots or [],**kwargs)


class HostVisibleCommentsTests(unittest.TestCase):
    def run_read(self, values, limit=1, replies=1, cursor=None):
        page=MagicMock();page.__enter__.return_value=page
        clock=[0.0]
        sequence=iter(values);last=[None]
        def observe(*args,**kwargs):
            try:last[0]=next(sequence)
            except StopIteration:pass
            if isinstance(last[0],Exception):raise last[0]
            return last[0]
        def pause(cancel,seconds):clock[0]+=seconds
        with patch('social_read.zhihu_comments.ReadPage',return_value=page) as pages,patch('social_read.zhihu_comments.snapshot',side_effect=observe) as probes,patch('social_read.zhihu_comments._pause',side_effect=pause),patch('social_read.zhihu_comments.time.monotonic',side_effect=lambda:clock[0]):
            result=read_visible_comments(None,TARGET,limit,replies,threading.Event(),cursor)
        self.assertEqual(pages.call_count,1)
        return result,probes

    def test_opens_once_reads_visible_replies_without_expansion_or_api(self):
        reply={'id':'3','text':'可见回复','parent_id':'1','is_reply':True}
        value,probes=self.run_read([snap(),snap(clicked=True),snap([entry(children=[reply])]),snap([entry(children=[reply])])])
        self.assertEqual(value['coverage']['returned_top_level'],1)
        self.assertEqual(value['coverage']['returned_replies'],1)
        self.assertEqual(value['coverage']['loading']['comment_panel_clicks'],1)
        self.assertEqual(value['coverage']['loading']['direct_api_requests'],0)
        self.assertEqual(value['coverage']['sort'],'platform_default')
        self.assertEqual([c.args[4] for c in probes.call_args_list],[None,'open',None,None])

    def test_unexpanded_reply_is_reported_without_click(self):
        value,probes=self.run_read([snap(),snap(clicked=True),snap([entry(expandable=True)]),snap([entry(expandable=True)])])
        self.assertEqual(value['coverage']['reply_roots_not_expanded'],1)
        self.assertEqual(value['coverage']['loading']['reply_expansions'],0)
        self.assertEqual(probes.call_count,4)

    def test_old_api_cursor_rejected_before_opening_browser(self):
        with patch('social_read.zhihu_comments.ReadPage') as pages,self.assertRaises(ReadError) as caught:
            read_visible_comments(None,TARGET,1,1,threading.Event(),{'offset':'next'})
        self.assertEqual(caught.exception.code,'cursor_expired');pages.assert_not_called()

    def test_unknown_open_result_is_not_replayed(self):
        with patch('social_read.zhihu_comments.ReadPage') as pages,patch('social_read.zhihu_comments.snapshot',side_effect=[snap(),ReadError('command_outcome_unknown','unknown')]) as probes,patch('social_read.zhihu_comments._pause'):
            with self.assertRaises(ReadError):read_visible_comments(None,TARGET,1,1,threading.Event())
        self.assertEqual(probes.call_count,2);self.assertEqual(pages.call_count,1)

    def test_explicit_gate_stops_before_panel_click(self):
        for status in ['verification_required','login_required','rate_limited','access_denied']:
            with self.subTest(status=status),patch('social_read.zhihu_comments.ReadPage'),patch('social_read.zhihu_comments.snapshot',side_effect=ReadError(status,'stop')) as probes:
                with self.assertRaises(ReadError):read_visible_comments(None,TARGET,1,1,threading.Event())
                self.assertEqual(probes.call_count,1)

    def test_gate_after_rows_preserves_them_without_continuation(self):
        value,probes=self.run_read([snap(),snap(clicked=True),snap([entry()]),ReadError('verification_required','stop')],limit=2)
        self.assertEqual(value['items'][0]['id'],'1')
        self.assertEqual(value['errors'][0]['status'],'verification_required')
        self.assertIsNone(value['_continuation']);self.assertEqual(probes.call_count,4)

    def test_visible_empty_is_distinct_from_not_loaded(self):
        value,_=self.run_read([snap(),snap(clicked=True),snap(ended=True,empty=True)],limit=2)
        self.assertEqual(value['coverage']['stop_reason'],'visible_empty')
        self.assertTrue(value['coverage']['root_end_reached']);self.assertIsNone(value['_continuation'])

    def test_incomplete_visible_sample_is_partial_instead_of_empty_success(self):
        value,_=self.run_read([snap(),snap(clicked=True),snap([entry()]),snap([entry()]),snap([entry()]),snap([entry()],scrolled=False)],limit=2)
        self.assertEqual(value['errors'][0]['status'],'comments_loading_incomplete')
        self.assertEqual(value['coverage']['returned_top_level'],1)

    def test_cursor_skips_seen_rows_and_does_not_loop_forever_on_empty_selection(self):
        original=entry('1')
        value,probes=self.run_read([snap(),snap(clicked=True),snap([original]),snap([original]),snap([original]),snap([original],scrolled=False)],cursor={'reader':CURSOR_READER,'seen':['1']})
        self.assertEqual(value['items'],[])
        self.assertEqual(value['errors'][0]['status'],'comments_loading_incomplete')
        self.assertGreater(probes.call_count,6)

    def test_heading_counts_replies_without_requiring_a_second_root(self):
        reply={'id':'3','text':'可见回复','parent_id':'1','is_reply':True}
        panel=snap([entry(children=[reply])],reported_comment_count=2,rendered_comment_count=2)
        value,probes=self.run_read([snap(),snap(clicked=True),panel,panel,panel],limit=2)
        self.assertEqual(value['errors'],[])
        self.assertEqual(value['coverage']['stop_reason'],'reported_visible_count_reached')
        self.assertEqual(value['coverage']['returned_top_level'],1)
        self.assertEqual(value['coverage']['returned_replies'],1)
        self.assertIsNone(value['coverage']['root_end_reached'])
        self.assertFalse(value['coverage']['all_comments']);self.assertFalse(value['coverage']['all_replies'])
        self.assertIsNone(value['_continuation'])
        self.assertNotIn('scroll',[c.args[4] for c in probes.call_args_list])

    def test_delayed_initial_rows_do_not_trigger_noop_scroll_or_early_stop(self):
        panel=snap([entry()],reported_comment_count=1,rendered_comment_count=1)
        value,probes=self.run_read([snap(),snap(clicked=True)]+[snap()]*8+[panel,panel,panel],limit=2)
        self.assertEqual(value['errors'],[])
        self.assertEqual(value['coverage']['returned_top_level'],1)
        self.assertEqual(probes.call_count,13)
        self.assertNotIn('scroll',[c.args[4] for c in probes.call_args_list])

    def test_loading_indicator_prevents_count_based_stop(self):
        busy=snap([entry()],reported_comment_count=1,rendered_comment_count=1,loading=True)
        ready=snap([entry()],reported_comment_count=1,rendered_comment_count=1)
        value,probes=self.run_read([snap(),snap(clicked=True)]+[busy]*8+[ready]*3,limit=2)
        self.assertEqual(probes.call_count,13)
        self.assertEqual(value['coverage']['stop_reason'],'reported_visible_count_reached')

    def test_unexpanded_replies_do_not_imply_all_visible_count_reached(self):
        panel=snap([entry(expandable=True)],reported_comment_count=1,rendered_comment_count=1)
        value,_=self.run_read([snap(),snap(clicked=True),panel],limit=2)
        self.assertEqual(value['errors'][0]['status'],'comments_loading_incomplete')
        self.assertEqual(value['coverage']['reply_roots_not_expanded'],1)
        self.assertEqual(value['coverage']['loading']['reply_expansions'],0)

    def test_noop_scroll_waits_for_later_root_and_is_not_replayed(self):
        initial=snap([entry()]);later=snap([entry(),entry('2')])
        value,probes=self.run_read([snap(),snap(clicked=True)]+[initial]*3+[snap([entry()],scrolled=False)]+[initial]*3+[later,later],limit=2)
        self.assertEqual(value['errors'],[])
        self.assertEqual(value['coverage']['returned_top_level'],2)
        self.assertEqual([c.args[4] for c in probes.call_args_list].count('scroll'),1)

    def test_answer_routes_to_dom_and_never_falls_back_to_denied_api(self):
        with patch('social_read.zhihu_comments.read_visible_comments',side_effect=ReadError('access_denied','stop')) as dom,patch('social_read.comments.read_api_comments') as api:
            with self.assertRaises(ReadError):read_comments(None,'zhihu',TARGET,2,1,threading.Event())
        dom.assert_called_once();api.assert_not_called()

    def test_article_keeps_separate_existing_path(self):
        with patch('social_read.comments.read_api_comments',return_value={'existing':True}) as api:
            value=read_comments(None,'zhihu','https://zhuanlan.zhihu.com/p/123',1,0,threading.Event())
        self.assertTrue(value['existing']);api.assert_called_once()

    def test_engine_keeps_reader_reported_default_sort(self):
        class Adapter(DemoAdapter):
            def comments(self,*args,**kwargs):
                return {'items':[{'id':'1','text':'评论'}],'coverage':{'sort':'platform_default'},'_continuation':None,'errors':[]}
        with tempfile.TemporaryDirectory() as td:
            engine=Engine(td,'demo',adapter=Adapter())
            try:
                result=engine.wait(engine.submit('comments',{'platform':'zhihu','url':TARGET})['task_id'],3)
                self.assertEqual(result['result']['coverage']['sort'],'platform_default')
            finally:engine.shutdown()


class DOMScriptTests(unittest.TestCase):
    def test_actual_script_checks_identity_and_parent_relationships_without_fetch(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node required')
        harness=r"""
          global.window={__socialReadZhihuComments:{operation:'fixture',id:'456',open_dispatched:true}};
          global.location={hostname:'www.zhihu.com',pathname:'/question/123/answer/456'};
          global.fetch=()=>{throw Error('direct API must never run')};
          global.setTimeout=()=>{throw Error('no page timers')};
          global.getComputedStyle=()=>({visibility:'visible',overflowY:'visible'});
          const visible={getClientRects:()=>[{}]};
          const panel={...visible,innerText:'5 条评论',classList:{contains:s=>s==='Modal-content'},dataset:{},contains:e=>e===root1||e===root2||e===reply};
          const make=(id,text,parent)=>{
            const n={...visible,id,parentElement:parent,children:[],closest:s=>s==='[data-id]'?n:null,getAttribute:k=>k==='data-id'?id:null,contains:e=>n.children.includes(e)};
            n.content={...visible,innerText:text,textContent:text,closest:()=>n,nextElementSibling:null,querySelector:()=>null,cloneNode:()=>({textContent:text,querySelectorAll:()=>[]})};
            n.author={innerText:'author'+id,closest:()=>n};
            n.querySelectorAll=s=>s==='.CommentContent'?[n.content,...n.children.map(e=>e.content)]:s.includes('/people/')?[n.author,...n.children.map(e=>e.author)]:[];
            return n;
          };
          panel.closest=()=>null;panel.parentElement=null;
          const root1=make('1999999999999999999','root one',panel),root2=make('2','root two',panel),reply=make('3','child',root2);root2.children=[reply];
          const answer={getAttribute:k=>k==='name'?'456':null};
          const button={...visible,innerText:'收起评论',closest:()=>answer};answer.querySelectorAll=()=>[button];
          panel.querySelector=s=>s==='.CommentContent'?root1.content:null;
          panel.querySelectorAll=s=>s==='[data-id]'?[root1,root2,reply]:[];
          global.document={title:'answer',body:{innerText:'answer'},querySelectorAll:s=>s==='.AnswerItem'?[answer]:[panel]};
          const result=SCRIPT;console.log(JSON.stringify(result));
        """
        class Page:
            def evaluate(self,script,*args):
                result=subprocess.run([node,'-e',harness.replace('SCRIPT',script)],text=True,capture_output=True,timeout=4)
                if result.returncode:raise AssertionError(result.stderr)
                return json.loads(result.stdout)
        value=snapshot(Page(),'456','fixture',1)
        self.assertEqual([r['root']['id'] for r in value['roots']],['1999999999999999999','2'])
        self.assertEqual(value['roots'][1]['children'][0]['parent_id'],'2')
        self.assertEqual(value['reported_comment_count'],5)
        self.assertEqual(value['rendered_comment_count'],3)
        self.assertFalse(value['ended'])
        original_harness=harness
        leaf="{children:[],innerText:'没有更多了',closest:()=>root1}"
        harness=original_harness.replace("s==='[data-id]'?[root1,root2,reply]:[]", "s==='[data-id]'?[root1,root2,reply]:s==='span,div,p'?["+leaf+"]:[]")
        self.assertFalse(snapshot(Page(),'456','fixture',1)['ended'])
        harness=harness.replace("closest:()=>root1}","closest:()=>null}")
        self.assertTrue(snapshot(Page(),'456','fixture',1)['ended'])
        harness=original_harness
        with self.assertRaises(ReadError) as caught:snapshot(Page(),'999','fixture',1)
        self.assertEqual(caught.exception.code,'source_mismatch')
