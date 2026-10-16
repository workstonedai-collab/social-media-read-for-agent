import json
import shutil
import subprocess
import threading
import unittest
from unittest.mock import MagicMock, patch

from social_read.browser_page import ReadPage
from social_read.comments import _xhs_snapshot, xhs_comments
from social_read.model import ReadError

TARGET='https://www.xiaohongshu.com/explore/000000000000000000000001?xsec_token=PRIVATE'

def entry(ident='root',children=None,expandable=False):
    return {'root':{'id':ident,'text':'评论正文','author':'author'},'children':children or [],'expandable':expandable}

def snap(roots,ended=False,clicked=False):
    return {'roots':roots,'available':len(roots),'count':len(roots),'ended':ended,'empty':ended and not roots,'clicked':clicked}

class HostCommentsTests(unittest.TestCase):
    def run_read(self, values, limit=1, replies=1, cursor=None):
        page=MagicMock();page.__enter__.return_value=page
        with patch('social_read.comments.ReadPage',return_value=page) as pages,patch('social_read.comments._xhs_snapshot',side_effect=values) as snapshots,patch('social_read.comments._pause'):
            result=xhs_comments(None,TARGET,limit,replies,threading.Event(),cursor)
        self.assertEqual(pages.call_count,1)
        return result,page,snapshots

    def test_image_only_note_does_not_wait_for_caption_or_title(self):
        value,page,_=self.run_read([snap([entry()])],replies=0)
        self.assertEqual(value['coverage']['returned_top_level'],1)
        self.assertNotIn('detail-title',page.wait.call_args.args[0])
        self.assertNotIn('detail-desc',page.wait.call_args.args[0])
        self.assertEqual(value['coverage']['loading']['page_attempts'],1)
        page.navigate_same_origin.assert_called_once_with(TARGET)

    def test_visible_replies_do_not_trigger_unnecessary_expansion(self):
        reply={'id':'reply','parent_id':'root','text':'回复','is_reply':True}
        value,_,calls=self.run_read([snap([entry(children=[reply],expandable=True)])])
        self.assertEqual(calls.call_count,1)
        self.assertEqual(value['coverage']['returned_replies'],1)
        self.assertEqual(value['coverage']['reply_requests'],0)

    def test_one_expansion_then_fresh_snapshot_with_host_wait(self):
        original=entry(expandable=True)
        reply={'id':'reply','parent_id':'root','text':'回复','is_reply':True}
        value,_,calls=self.run_read([snap([original]),snap([original],clicked=True),snap([entry(children=[reply])])])
        self.assertEqual(calls.call_count,3)
        self.assertEqual(calls.call_args_list[1].args[-2:],('expand','root'))
        self.assertEqual(value['coverage']['reply_requests'],1)
        self.assertEqual(value['coverage']['returned_replies'],1)

    def test_unknown_scroll_outcome_preserves_observed_comment_and_stops(self):
        value,_,calls=self.run_read([snap([entry()]),ReadError('command_result_unknown','unknown')],limit=2,replies=0)
        self.assertEqual(calls.call_count,2)
        self.assertEqual(value['items'][0]['id'],'root')
        self.assertEqual(value['errors'][0]['status'],'command_result_unknown')
        self.assertEqual(value['coverage']['stop_reason'],'read_interrupted')
        self.assertEqual(value['_continuation']['seen'],['root'])

    def test_gate_during_walk_does_not_discard_already_read_or_replay(self):
        value,_,calls=self.run_read([snap([entry()]),ReadError('verification_required','stop')],limit=2,replies=0)
        self.assertEqual(calls.call_count,2);self.assertEqual(len(value['items']),1)
        self.assertEqual(value['errors'][0]['status'],'verification_required')

    def test_visible_empty_marker_is_distinct_from_loading_stall(self):
        value,_,calls=self.run_read([snap([],ended=True)],limit=2,replies=0)
        self.assertEqual(calls.call_count,1);self.assertEqual(value['coverage']['stop_reason'],'visible_empty')
        self.assertTrue(value['coverage']['root_end_reached']);self.assertIsNone(value['_continuation'])

    def test_unseen_root_selection_and_resume_seen_set_are_preserved(self):
        value,_,calls=self.run_read([snap([entry('next')])],replies=0,cursor={'seen':['prior']})
        self.assertEqual(calls.call_args.args[2],{'prior'})
        self.assertEqual(set(value['_continuation']['seen']),{'prior','next'})

class BrowserScriptTests(unittest.TestCase):
    def node(self, source):
        node=shutil.which('node')
        if not node:self.skipTest('Node required for fixed script behavior')
        done=subprocess.run([node,'-e',source],text=True,capture_output=True,timeout=4)
        self.assertEqual(done.returncode,0,done.stderr)
        return json.loads(done.stdout)

    def test_comment_snapshot_needs_no_page_timer_and_checks_note_identity(self):
        owner=self
        class Page:
            def evaluate(self, script, timeout):
                source="""
                global.location={origin:'https://www.xiaohongshu.com',pathname:'/explore/000000000000000000000001'};
                global.setTimeout=()=>{throw new Error('background timers must not be used')};
                const content={innerText:'真实文字'};
                const item={id:'comment-root',querySelector:s=>s.includes('.content')?content:null};
                const parent={querySelector:s=>s==='.comment-item'?item:null,querySelectorAll:()=>[]};
                global.document={body:{innerText:'正常页面'},querySelector:()=>null,querySelectorAll:()=>[parent]};
                Promise.resolve(SCRIPT).then(v=>console.log(JSON.stringify(v))).catch(e=>{console.error(e);process.exit(1)});
                """.replace('SCRIPT',script)
                return owner.node(source)
        value=_xhs_snapshot(Page(),'000000000000000000000001',set(),1)
        self.assertEqual(value['roots'][0]['root']['text'],'真实文字')
        with self.assertRaises(ReadError) as error:_xhs_snapshot(Page(),'000000000000000000000002',set(),1)
        self.assertEqual(error.exception.code,'source_mismatch')

    def test_same_origin_navigation_is_idempotent_and_never_crosses_site(self):
        owner=self
        page=ReadPage(None,'https://www.xiaohongshu.com/explore',None)
        for initial,expected in [('https://www.xiaohongshu.com/explore',1),(TARGET,0)]:
            def evaluate(script,timeout):
                source='let assigns=0;global.location=new URL('+json.dumps(initial)+');location.assign=()=>assigns++;const receipt='+script+';console.log(JSON.stringify({receipt,assigns}));'
                result=owner.node(source);self.assertEqual(result['assigns'],expected);return result['receipt']
            page.evaluate=evaluate
            with patch('social_read.browser_page.wait_navigation') as navigation:
                page.navigate_same_origin(TARGET)
            self.assertEqual(navigation.call_args.kwargs['expected_path'],'/explore/000000000000000000000001')
        with self.assertRaises(ReadError):page.navigate_same_origin('https://evil.example/explore')

    def test_recommendation_cards_are_not_search_results_for_wrong_keyword(self):
        from social_read.xiaohongshu_search import search_notes
        owner=self;captured=[]
        class Page:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def navigate_same_origin(self,*args):pass
            def evaluate(self,script,*args):
                result=owner.node("global.location=new URL('https://www.xiaohongshu.com/explore');global.document={body:{innerText:'正常推荐'},querySelectorAll:()=>{throw new Error('must not collect recommendations')}};console.log(JSON.stringify("+script+"));")
                captured.append(result)
                raise ReadError(result['failure'],'not a search result')
        with patch('social_read.xiaohongshu_search.ReadPage',Page):
            with self.assertRaises(ReadError):search_notes(None,'主题',1,threading.Event())
        self.assertEqual(captured[0]['stage'],'search_navigation');self.assertEqual(captured[0]['items'],[])

if __name__=='__main__':unittest.main()
