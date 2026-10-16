"""Run the actual fixed request script against local, account-free responses."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from social_read.comments import read_api_comments as read_comments, finish
from social_read.adapter import DemoAdapter
from social_read.engine import Engine
from social_read.model import ReadError, request_diagnostics
from social_read.platform_reads import FETCH_JS, check_failure

NODE = shutil.which('node')
TARGET = 'https://www.zhihu.com/question/123/answer/456'


def execute(script, responses):
    harness = """const responses=RESPONSES, calls=[];let bodies=0;
// No real browser, cookies or network: this mock is the only fetch.
globalThis.fetch=async path=>{
  calls.push(path);const r=responses.shift();
  if(!r)throw Error('unexpected extra request');
  if(r.network_error)throw Error('local fixture connection failure');
  return {ok:r.status>=200 && r.status<300,status:r.status,
    headers:{get:()=>r.type || ''},text:async()=>{bodies++;if(r.body_error)throw Error('local fixture body failure');return r.body || '';}};
};
(async()=>{const value=await SCRIPT;console.log(JSON.stringify({value,calls,bodies}));})().catch(e=>{console.error(e);process.exitCode=1;});
""".replace('RESPONSES', json.dumps(responses)).replace('SCRIPT', script)
    result = subprocess.run([NODE, '-e', harness], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(NODE, 'Node.js required to execute fixed JavaScript response fixtures')
class RequestDiagnosticsTests(unittest.TestCase):
    def request(self, responses):
        return execute('(async()=>{' + FETCH_JS + "return await get('/api/v4/comment_v5/answers/456/root_comment?limit=2');})()", responses)

    def test_http403_is_denied_without_retry_or_response_body_leak(self):
        result=self.request([{'status':403,'type':'text/html','body':'PRIVATE account, Cookie, challenge URL'}])
        self.assertEqual(len(result['calls']),1);self.assertEqual(result['bodies'],0)
        self.assertEqual(result['value']['failure'],'access_denied')
        self.assertEqual(result['value']['diagnostics'],{'request_kind':'zhihu_root_comments','request_attempts':1,'http_status':403,'response_kind':'html','failure_origin':'http_response'})
        self.assertNotIn('PRIVATE',json.dumps(result['value']))

    def test_http403_known_json_challenge_is_verification_not_generic_denial(self):
        result=self.request([{'status':403,'type':'application/json; charset=utf-8','body':json.dumps({'error':{'code':40362,'message':'PRIVATE','redirect':'https://private.invalid'}})}])
        self.assertEqual(result['value']['failure'],'verification_required')
        self.assertEqual(result['value']['diagnostics']['platform_error_code'],40362)
        self.assertEqual(len(result['calls']),1);self.assertNotIn('PRIVATE',json.dumps(result['value']))

    def test_unknown_json_denial_preserves_status_without_claiming_a_cause(self):
        result=self.request([{'status':403,'type':'application/problem+json','body':'{"error":{"code":100,"message":"PRIVATE"}}'}])
        self.assertEqual(result['value']['failure'],'access_denied')
        self.assertEqual(result['value']['diagnostics']['platform_error_code'],100)
        self.assertEqual(len(result['calls']),1)

    def test_unreadable_denial_body_does_not_trigger_network_retry(self):
        result=self.request([{'status':403,'type':'application/json','body_error':True}])
        self.assertEqual(result['value']['failure'],'access_denied');self.assertEqual(len(result['calls']),1)

    def test_rate_limit_and_login_stop_once(self):
        for status,code in [(429,'rate_limited'),(401,'login_required')]:
            with self.subTest(status=status):
                result=self.request([{'status':status}])
                self.assertEqual(result['value']['failure'],code);self.assertEqual(len(result['calls']),1)

    def test_200_business_challenge_and_bad_json_are_distinct(self):
        result=self.request([{'status':200,'type':'application/json','body':'{"error":{"code":40362}}'}])
        self.assertEqual(result['value']['failure'],'verification_required')
        self.assertEqual(result['value']['diagnostics']['failure_origin'],'platform_json')
        result=self.request([{'status':200,'type':'text/html','body':'not json'}])
        self.assertEqual(result['value']['failure'],'adapter_incompatible')
        self.assertEqual(result['value']['diagnostics']['failure_origin'],'response_parse')

    def test_timeout_recovery_then_denial_does_not_make_a_third_request(self):
        result=self.request([{'network_error':True},{'status':403}])
        self.assertEqual(result['value']['failure'],'access_denied');self.assertEqual(len(result['calls']),2)
        self.assertEqual(result['value']['diagnostics']['request_attempts'],2)

    def test_actual_comment_adapter_keeps_root_http_denial_through_python_error(self):
        observations=[]
        class Page:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def evaluate(self,script,*args):
                result=execute(script,[{'status':403,'type':'text/html'}]);observations.append(result);return result['value']
        with patch('social_read.comments.ReadPage',Page),self.assertRaises(ReadError) as caught:
            read_comments(None,'zhihu',TARGET,2,1,threading.Event())
        value=caught.exception.result()
        self.assertEqual(value['diagnostics']['http_status'],403)
        self.assertEqual(value['diagnostics']['request_kind'],'zhihu_root_comments')
        self.assertEqual(len(observations[0]['calls']),1)

    def test_child_denial_keeps_roots_and_stops_other_child_requests(self):
        observations=[]
        root={'data':[{'id':'1','content':'one','child_comment_count':1},{'id':'2','content':'two','child_comment_count':1}],'paging':{'is_end':True}}
        class Page:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def evaluate(self,script,*args):
                result=execute(script,[{'status':200,'type':'application/json','body':json.dumps(root)},{'status':403,'type':'application/json','body':'{"error":{"code":40362}}'}]);observations.append(result);return result['value']
        with patch('social_read.comments.ReadPage',Page):
            result=read_comments(None,'zhihu',TARGET,2,1,threading.Event())
        self.assertEqual(len(observations[0]['calls']),2)
        self.assertEqual(result['coverage']['returned_top_level'],2)
        self.assertEqual(result['errors'][0]['status'],'verification_required')
        self.assertEqual(result['errors'][0]['diagnostics']['request_kind'],'zhihu_child_comments')


class DiagnosticBoundaryTests(unittest.TestCase):
    def test_engine_persists_denial_diagnostics_without_retry_or_empty_success(self):
        class Adapter(DemoAdapter):
            calls=0
            def comments(self,*args,**kwargs):
                self.calls+=1
                check_failure({'failure':'access_denied','diagnostics':{'http_status':403,'request_kind':'zhihu_root_comments','body':'PRIVATE'}})
        with tempfile.TemporaryDirectory() as td:
            adapter=Adapter();engine=Engine(td,'demo',adapter=adapter)
            try:
                result=engine.wait(engine.submit('comments',{'platform':'zhihu','url':TARGET,'limit':2,'replies_limit':1})['task_id'],3)
                self.assertEqual(result['status'],'failed');self.assertEqual(adapter.calls,1)
                self.assertEqual(result['result']['errors'][0]['diagnostics'],{'http_status':403,'request_kind':'zhihu_root_comments'})
                self.assertNotIn('PRIVATE',json.dumps(result))
            finally:engine.shutdown()

    def test_allowlist_rejects_body_headers_urls_and_invalid_field_values(self):
        raw={'http_status':403,'request_kind':'zhihu_root_comments','request_attempts':1,
             'headers':{'Cookie':'PRIVATE'},'url':'https://private.invalid','body':'PRIVATE','platform_error_code':'PRIVATE',
             'failure_origin':'PRIVATE','response_kind':'PRIVATE'}
        safe=request_diagnostics(raw)
        self.assertEqual(safe,{'http_status':403,'request_kind':'zhihu_root_comments','request_attempts':1})
        with self.assertRaises(ReadError) as caught:check_failure({'failure':'access_denied','diagnostics':raw})
        self.assertEqual(caught.exception.result()['diagnostics'],safe)
        self.assertNotIn('PRIVATE',json.dumps(caught.exception.result()))
        self.assertEqual(request_diagnostics({'http_status':True,'request_attempts':99,'platform_error_code':10**15}),{})

    def test_partial_comment_diagnostics_are_filtered_too(self):
        result=finish({'items':[],'errors':[{'status':'access_denied','diagnostics':{'http_status':403,'body':'PRIVATE'}}]},'zhihu',2,1)
        self.assertEqual(result['errors'][0]['diagnostics'],{'http_status':403})
        self.assertNotIn('PRIVATE',json.dumps(result))
