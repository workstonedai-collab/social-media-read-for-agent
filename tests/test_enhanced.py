import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from social_read.images import image_url, enrich_images, ImageRedirect
from social_read.model import ReadError, content_item, digest
from social_read.comments import normalize_rows, finish
from social_read.engine import Engine
from social_read.adapter import DemoAdapter, Runner
from social_read.mcp import call
from social_read.platform_reads import read_weibo
from social_read.browser_page import ReadPage
from social_read.article_text import html_text

XHS="https://www.xiaohongshu.com/explore/000000000000000000000001?xsec_token=PRIVATE"
WB="https://weibo.com/100000/AbCd"
IMAGE="https://sns-webpic.xhscdn.com/one"

class ImageTests(unittest.TestCase):
    def test_image_origin_and_redirect_boundary(self):
        self.assertEqual(image_url("http://sns-webpic.xhscdn.com/one"),IMAGE)
        for u in ["https://xhscdn.com.evil.org/a","https://evil-xhscdn.com/a","https://evil.org/xhscdn.com/a","https://u@xhscdn.com/a","https://xhscdn.com:9999/a","file:///etc/passwd"]:
            with self.subTest(u=u),self.assertRaises(ReadError):image_url(u)
        with self.assertRaises(ReadError):ImageRedirect().redirect_request(None,None,302,"",{},"https://evil.org/a")

    def test_partial_image_read_keeps_caption_order_and_body_hash(self):
        with tempfile.TemporaryDirectory() as td:
            runner=Runner(td,profile="dedicated")
            item=content_item("xiaohongshu",{"content":"笔记配文"},XHS,"test")
            values=[{"status":"success","text":"图中文字","text_chars":4,"lines":[{"text":"图中文字"}]},
                    ReadError("image_unavailable","图片不可用")]
            with patch("social_read.images.LocalOCR.read",side_effect=values):
                result=enrich_images(item,[IMAGE,IMAGE,IMAGE+"2"],runner,threading.Event(),"note_state_carousel",2)
            self.assertEqual(result["coverage"]["discovered_images"],2)
            self.assertEqual(result["coverage"]["ocr_unread"],1)
            self.assertEqual([i["order"] for i in result["image_ocr"]],[1,2])
            self.assertNotIn("lines",result["image_ocr"][0])
            self.assertIn("笔记配文",result["body"]);self.assertIn("图中文字",result["body"])
            self.assertEqual(result["body_sha256"],digest(result["body"]))
            self.assertEqual(result["read_errors"][0]["image_order"],2)

    def test_image_only_and_no_text_are_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            item=content_item("xiaohongshu",{"content":"（没有配文）"},XHS,"test")
            with patch("social_read.images.LocalOCR.read",return_value={"status":"no_text_detected","text":"","text_chars":0}):
                result=enrich_images(item,[IMAGE],Runner(td),threading.Event(),"page_dom_unverified",1)
            self.assertEqual(result["image_ocr"][0]["status"],"no_text_detected")
            self.assertFalse(result["coverage"]["ocr_accuracy_verified"])
            self.assertTrue(result["coverage"]["images_all_processed"])

class CommentTests(unittest.TestCase):
    def test_exact_large_ids_and_reply_limits(self):
        raw=[{"id":"1999999999999999999","text":"首层","author":"甲"},
             {"id":"2000000000000000001","parent_id":"1999999999999999999","text":"回复一","author":"乙"},
             {"id":"2000000000000000002","parent_id":"1999999999999999999","text":"回复二","author":"丙"},
             {"id":"2000000000000000003","text":"另一首层"}]
        rows=normalize_rows(raw,"zhihu",1,1)
        self.assertEqual(len(rows),2);self.assertEqual(rows[1]["parent_id"],raw[0]["id"])
        self.assertEqual(rows[1]["id"],"2000000000000000001")

    def test_local_ids_stable_and_not_mislabeled_platform_ids(self):
        rows=normalize_rows([{"text":"评论","author":"甲","time":"2026-10-15"}],"xiaohongshu",3,2)
        again=normalize_rows([{"text":"评论","author":"甲","time":"2026-10-15"}],"xiaohongshu",3,2)
        self.assertEqual(rows[0]["id"],again[0]["id"])
        self.assertEqual(rows[0]["id_source"],"local_content_hash")

    def test_empty_comments_are_not_proof_of_zero(self):
        value=finish({"items":[],"is_end":True},"weibo",5,2)
        self.assertEqual(value["coverage"]["returned_rows"],0)
        self.assertFalse(value["coverage"]["all_comments"])
        self.assertIn("不能据此判断",value["notice"])

    def test_stalled_comments_return_loading_error_instead_of_empty_success(self):
        value=finish({"items":[],"is_end":None,"stop_reason":"loading_stalled"},"xiaohongshu",3,1)
        self.assertEqual(value["errors"][0]["status"],"comments_loading_incomplete")
        self.assertEqual(value["coverage"]["returned_rows"],0)

    def test_challenge_never_becomes_empty_success(self):
        with self.assertRaises(ReadError) as exc:finish({"failure":"verification_required"},"zhihu",5,2)
        self.assertEqual(exc.exception.code,"verification_required")

class LongReadTests(unittest.TestCase):
    def test_plain_math_and_multiline_are_not_stripped_as_markup(self):
        text="第一段\n\n若 a < b 且 c > d，则继续"
        self.assertEqual(content_item("weibo",{"text":text,"text_format":"plain"},WB,"test")["body"],text)
        self.assertEqual(html_text("<p>第一段</p><p>第二段<br>第三行</p><script>noise</script>"),"第一段\n\n第二段\n第三行")

    def test_failed_long_expand_keeps_partial_text_and_error(self):
        class Page:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def evaluate(self,*a):return {"author":"甲","text":{"content":"当前摘要","format":"plain","long_text":True,"expanded":False,"status":"access_denied"},"retweet":None}
        with patch("social_read.platform_reads.ReadPage",Page):
            value=read_weibo(None,WB,threading.Event())
        self.assertEqual(value["body"],"当前摘要")
        self.assertFalse(value["coverage"]["long_text_expanded"])
        self.assertEqual(value["read_errors"][0]["status"],"access_denied")

    def test_owned_tab_closes_after_parser_error(self):
        with tempfile.TemporaryDirectory() as td:
            runner=Runner(td,profile="dedicated");calls=[]
            def run(args,cancel=None,timeout=None):
                calls.append(args)
                if args[2:4]==["tab","list"]:return 0,'[{"page":"owned","url":"https://weibo.com/100000/AbCd"}]',""
                return (0,'{"page":"owned"}','') if args[2:4]==["tab","new"] else (0,'{"ready":true}',"")
            with patch.object(runner,"run",side_effect=run):
                with self.assertRaises(ValueError):
                    with ReadPage(runner,WB,threading.Event()):raise ValueError()
            self.assertEqual(calls[-1][2:],["tab","close","owned"])

class EnhancedEngineTests(unittest.TestCase):
    def test_partial_images_preserve_content_and_partial_on_cached_read(self):
        class Partial(DemoAdapter):
            def read(self,*args):
                result=super().read(*args)
                result["read_errors"]=[{"status":"image_unavailable","message":"一张图片未读"}]
                result["coverage"]={"ocr_unread":1}
                return result
        with tempfile.TemporaryDirectory() as td:
            e=Engine(td,"demo",adapter=Partial())
            try:
                first=e.wait(e.submit("read",{"platform":"weibo","url":WB})["task_id"],3)
                second=e.wait(e.submit("read",{"content_ref":first["result"]["content"]["content_ref"]})["task_id"],3)
                self.assertEqual(first["status"],"partial");self.assertEqual(second["status"],"partial")
                self.assertTrue(second["result"]["content"]["cache"]["used"])
            finally:e.shutdown()

    def test_reply_limit_validated_before_task(self):
        with tempfile.TemporaryDirectory() as td:
            e=Engine(td,"demo")
            try:
                for val in [True,6,-1]:
                    with self.assertRaises(ReadError):call(e,"read_comments",{"platform":"weibo","url":WB,"replies_limit":val})
                self.assertEqual(len(e.store.history()),0)
            finally:e.shutdown()

class ErrorClassificationTests(unittest.TestCase):
    def test_unknown_operation_outcome_never_becomes_retryable_timeout(self):
        from social_read.model import classify_error
        error=classify_error('command_result_unknown browser extension timeout')
        self.assertEqual(error.code,'command_outcome_unknown');self.assertFalse(error.retryable)

    def test_process_id_is_not_http_429(self):
        from social_read.model import classify_error
        self.assertEqual(classify_error("(node:84290) TypeError: observe target is not a Node").code,"adapter_error")
        self.assertEqual(classify_error("HTTP 429 Too Many Requests").code,"rate_limited")

    def test_empty_platform_results_are_partial_not_success(self):
        class Empty(DemoAdapter):
            def search(self,site,*a):return [] if site=="xiaohongshu" else super().search(site,*a)
        with tempfile.TemporaryDirectory() as td:
            e=Engine(td,"demo",adapter=Empty())
            try:
                j=e.wait(e.submit("search",{"platforms":["xiaohongshu","weibo"],"query":"主题"})["task_id"],3)
                self.assertEqual(j["status"],"partial")
                self.assertEqual(j["result"]["errors"][0]["status"],"empty")
            finally:e.shutdown()

class ImageCacheTests(unittest.TestCase):
    def test_short_term_image_cache_avoids_repeated_download_and_expires(self):
        from social_read.images import LocalOCR
        with tempfile.TemporaryDirectory() as td:
            ocr=LocalOCR(td)
            def download(url,path,cancel,deadline=None):Path(path).write_bytes(b"same-image-bytes")
            response=json.dumps({"status":"success","lines":[{"text":"真实图中文字"}],"width":100,"height":100})
            with patch.object(ocr,"command",return_value=["/native/ocr"]),patch("social_read.images.download_image",side_effect=download) as get,patch("social_read.images.run_local",return_value=response):
                first=ocr.read(IMAGE,threading.Event())
                second=ocr.read(IMAGE,threading.Event())
                self.assertEqual(get.call_count,1)
                self.assertFalse(first["image_download_cache_used"])
                self.assertTrue(second["image_download_cache_used"])
                self.assertEqual(first["image_sha256"],second["image_sha256"])
                import time
                with patch("social_read.images.time.time",return_value=time.time()+1801):
                    ocr.read(IMAGE,threading.Event())
                self.assertEqual(get.call_count,2)

class BodyReadinessTests(unittest.TestCase):
    def test_blank_page_and_absent_body_do_not_pass_readiness(self):
        import shutil,subprocess
        from social_read.browser_page import wait_selector
        node=shutil.which("node")
        if not node:self.skipTest("Node runtime not installed")
        class Runner:
            calls=0
            def run(self,args,cancel=None,timeout=None):
                self.calls+=1
                # A blank document has a body, then the real document briefly
                # has no body; only the third sample can satisfy this wait.
                protocol='about:' if self.calls==1 else 'https:'
                body=self.calls!=2
                harness="global.location={protocol:"+json.dumps(protocol)+"};global.document={body:null,querySelector:()=>"+str(body).lower()+"};console.log(JSON.stringify("+args[3]+"));"
                p=subprocess.run([node,"-e",harness],text=True,capture_output=True,timeout=3)
                return p.returncode,p.stdout,p.stderr
        runner=Runner()
        wait_selector(runner,"owned","tab","body",threading.Event(),3)
        self.assertEqual(runner.calls,3)

    def test_native_navigation_waits_for_owned_target_not_another_tab(self):
        from social_read.browser_page import wait_navigation
        class Runner:
            calls=0
            def run(self,args,cancel=None,timeout=None):
                self.calls+=1
                assert args==["browser","owned","tab","list"]
                tabs=[{"page":"other","url":"https://example.org"},{"page":"target","url":"about:blank" if self.calls==1 else "https://weixin.sogou.com/weixin"}]
                return 0,json.dumps(tabs),""
        runner=Runner();wait_navigation(runner,"owned","target",threading.Event(),2)
        self.assertEqual(runner.calls,2)

    def test_verification_stops_without_extra_probes(self):
        from social_read.browser_page import wait_selector
        from unittest.mock import Mock
        runner=Mock();runner.run.return_value=(0,'{"ready":false,"verification":true}',"")
        with self.assertRaises(ReadError) as exc:wait_selector(runner,"owned","tab","#js_content",threading.Event(),2)
        self.assertEqual(exc.exception.code,"verification_required");self.assertEqual(runner.run.call_count,1)

    def test_context_change_recovers_but_unknown_outcome_does_not(self):
        from social_read.browser_page import wait_selector
        from unittest.mock import Mock
        runner=Mock();runner.run.side_effect=[(1,"Inspected target navigated", ""),(0,'{"ready":true}',"")]
        wait_selector(runner,"owned","tab","#js_content",threading.Event(),2)
        self.assertEqual(runner.run.call_count,2)
        runner=Mock();runner.run.return_value=(1,"command_result_unknown Inspected target navigated","")
        with self.assertRaises(ReadError):wait_selector(runner,"owned","tab","#js_content",threading.Event(),2)
        self.assertEqual(runner.run.call_count,1)

    def test_first_probe_keeps_bridge_connection_budget_within_total_deadline(self):
        from social_read.browser_page import wait_selector
        from unittest.mock import Mock
        runner=Mock();runner.run.return_value=(0,'{"ready":true}',"")
        wait_selector(runner,"owned","tab","section",threading.Event(),20)
        budget=runner.run.call_args.args[2]
        self.assertGreater(budget,8);self.assertLessEqual(budget,20)

    def test_ready_article_mentioning_verification_is_readable(self):
        from social_read.browser_page import wait_selector
        from unittest.mock import Mock
        runner=Mock();runner.run.return_value=(0,'{"ready":true,"verification":true}',"")
        wait_selector(runner,"owned","tab","#js_content",threading.Event(),1)

    def test_navigation_deadline_and_cancellation(self):
        from social_read.browser_page import wait_navigation
        from unittest.mock import Mock
        runner=Mock();runner.run.return_value=(0,'[{"page":"target","url":"about:blank"}]',"")
        with self.assertRaises(ReadError) as exc:wait_navigation(runner,"owned","target",threading.Event(),.05)
        self.assertEqual(exc.exception.code,"navigation_loading_timeout")
        event=threading.Event();event.set();runner.reset_mock()
        with self.assertRaises(ReadError) as exc:wait_navigation(runner,"owned","target",event,2)
        self.assertEqual(exc.exception.code,"cancelled");runner.run.assert_not_called()
