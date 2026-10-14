import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from social_read.adapter import DemoAdapter, Runner, OpenCLIAdapter
from social_read.engine import Engine
from social_read.mcp import Protocol, TOOLS, call
from social_read.model import ReadError, classify_error, content_item, public_url, scrub, search_item, validate_url
from social_read.storage import Store
from social_read.web import handler_for

XHS = "https://www.xiaohongshu.com/explore/000000000000000000000001?xsec_token=PRIVATE_SIGNATURE"
WB = "https://weibo.com/1000000000/AbCdDemo"


class ModelTests(unittest.TestCase):
    def test_allowed_targets(self):
        self.assertEqual(validate_url("xiaohongshu", XHS), XHS)
        self.assertEqual(validate_url("weibo", WB), WB)
        self.assertTrue(validate_url("weixin", "http://mp.weixin.qq.com/s/abc").startswith("https:"))

    def test_non_platform_and_management_urls_rejected(self):
        bad = ["https://127.0.0.1/s/a", "https://mp.weixin.qq.com.evil.org/s/a", "https://evil@mp.weixin.qq.com/s/a", "https://mp.weixin.qq.com:9999/s/a", "file:///etc/passwd", "https://mp.weixin.qq.com/cgi-bin/home", "https://weixin.sogou.com/link\nX-Header:abc"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ReadError):
                validate_url("weixin", url)

    def test_recursive_secret_redaction(self):
        value = scrub({"cookie": "SECRET", "nested": [{"url": XHS, "text": "access_token=HIDDEN"}]})
        self.assertNotIn("PRIVATE_SIGNATURE", json.dumps(value))
        self.assertNotIn("HIDDEN", json.dumps(value))
        self.assertNotIn("cookie", value)
        self.assertEqual(public_url(XHS), XHS.split("?")[0])

    def test_generic_error_is_not_account_ban(self):
        self.assertEqual(classify_error("HTTP 406").code, "adapter_error")
        self.assertEqual(classify_error("EPERM: operation not permitted").code, "environment_restricted")
        self.assertEqual(classify_error("SECURITY_BLOCK captcha").code, "verification_required")

    def test_empty_body_never_becomes_complete(self):
        with self.assertRaises(ReadError):
            content_item("weibo", {"title": "article", "text": ""}, WB, "test")
        d = content_item("weibo", {"text": "可见内容", "likes": 0}, WB, "test")
        self.assertEqual(d["completeness"], "unverified")


class StoreTests(unittest.TestCase):
    def test_private_target_and_search_does_not_overwrite_body(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(td)
            found = store.save_item("xiaohongshu", search_item("xiaohongshu", {"title": "笔记", "url": XHS}, "主题", "test"))
            ref = found["content_ref"]
            self.assertNotIn("PRIVATE_SIGNATURE", json.dumps(found))
            self.assertEqual(store.item(ref)["target"], XHS)
            body = content_item("xiaohongshu", {"content": "实际正文"}, XHS, "test")
            store.save_item("xiaohongshu", body, read=True)
            store.save_item("xiaohongshu", search_item("xiaohongshu", {"title": "新版标题", "url": XHS}, "主题", "test"))
            self.assertEqual(store.item(ref)["data"]["body"], "实际正文")
            self.assertEqual(os.stat(store.path).st_mode & 0o777, 0o600)
            store.close()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name, mode="demo")

    def tearDown(self):
        self.engine.shutdown()
        self.temp.cleanup()

    def task(self, action, request):
        return self.engine.wait(self.engine.submit(action, request)["task_id"], 3)

    def test_search_read_comments_and_cache(self):
        search = self.task("search", {"platforms": ["xiaohongshu", "weibo", "zhihu", "weixin"], "query": "知识管理"})
        self.assertEqual(search["status"], "success")
        self.assertEqual(len(search["result"]["items"]), 4)
        ref = search["result"]["items"][0]["content_ref"]
        read = self.task("read", {"content_ref": ref})
        self.assertEqual(read["result"]["content"]["mode"], "demo")
        self.assertFalse(read["result"]["content"]["cache"]["used"])
        self.assertTrue(self.task("read", {"content_ref": ref})["result"]["content"]["cache"]["used"])
        self.assertFalse(self.task("read", {"content_ref": ref, "refresh": True})["result"]["content"]["cache"]["used"])
        self.assertFalse(self.task("comments", {"content_ref": ref})["result"]["coverage"]["all_comments"])
        self.assertNotIn("DEMO_ONLY", json.dumps(self.engine.store.history()))

    def test_partial_success_preserves_other_platform(self):
        class Partial(DemoAdapter):
            def search(self, site, query, limit, cancel):
                if site == "weibo":
                    raise ReadError("login_required", "请登录")
                return super().search(site, query, limit, cancel)
        self.engine.adapter = Partial()
        job = self.task("search", {"platforms": ["xiaohongshu", "weibo"], "query": "主题"})
        self.assertEqual(job["status"], "partial")
        self.assertEqual(len(job["result"]["items"]), 1)
        self.assertEqual(job["result"]["errors"][0]["status"], "login_required")

    def test_cancel_cooperative_read(self):
        class Slow(DemoAdapter):
            def search(self, site, query, limit, cancel):
                cancel.wait(2)
                return super().search(site, query, limit, cancel)
        self.engine.adapter = Slow()
        job = self.engine.submit("search", {"platforms": ["weibo"], "query": "主题"})
        self.engine.cancel(job["task_id"])
        self.assertEqual(self.engine.wait(job["task_id"], 3)["status"], "cancelled")

    def test_queries_and_actions_are_validated_before_job_created(self):
        for req in [{"platforms": ["weibo"], "query": "--help"}, {"platforms": ["weibo"], "query": "q", "command": "publish"}, {"platforms": ["weibo"], "query": "q", "limit": True}, {"platforms": ["weibo", "weibo"], "query": "q"}]:
            with self.assertRaises(ReadError):
                self.engine.submit("search", req)
        with self.assertRaises(ReadError):
            self.engine.submit("publish", {})
        self.assertEqual(len(self.engine.store.history()), 0)

    def test_mode_and_profile_state_are_isolated(self):
        other = Engine(self.temp.name, "live", profile="another")
        try:
            self.assertNotEqual(other.store.root, self.engine.store.root)
        finally:
            other.shutdown()

    def test_unknown_exception_is_not_leaked(self):
        class Broken(DemoAdapter):
            def search(self, *args):
                raise Exception("cookie=PRIVATE; " + XHS)
        self.engine.adapter = Broken()
        job = self.task("search", {"platforms": ["weibo"], "query": "q"})
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("PRIVATE", json.dumps(job))


class RunnerTests(unittest.TestCase):
    def test_write_never_reaches_subprocess(self):
        with tempfile.TemporaryDirectory() as td, patch("subprocess.Popen") as popen:
            runner = Runner(td, executable="opencli")
            with self.assertRaises(ReadError):
                runner.call("weibo", "publish", ["text"])
            with self.assertRaises(ReadError):
                runner.call("weixin", "create-draft", ["text"])
            popen.assert_not_called()

    def test_subprocess_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(td, executable=sys.executable, timeout=0.15)
            before = time.monotonic()
            with self.assertRaises(ReadError) as ctx:
                runner.run(["-c", "import time; time.sleep(10)"])
            self.assertEqual(ctx.exception.code, "timeout")
            self.assertLess(time.monotonic() - before, 3)

    def test_upstream_failure_redacts_fixed_error(self):
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(td, executable="opencli")
            with patch.object(runner, "run", return_value=(77, "cookie=PRIVATE access_token=SECRET AUTH_REQUIRED", "")):
                with self.assertRaises(ReadError) as ctx:
                    runner.call("weibo", "search", ["q"])
                self.assertNotIn("PRIVATE", ctx.exception.message)


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name, "demo")
        self.protocol = Protocol(self.engine)

    def tearDown(self):
        self.engine.shutdown()
        self.temp.cleanup()

    def request(self, method, params=None, ident=1):
        return self.protocol.response({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})

    def test_initialize_list_and_reject_write_tool(self):
        self.assertIn("error", self.request("tools/list"))
        result = self.request("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(result["result"]["protocolVersion"], "2025-06-18")
        self.protocol.response({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = self.request("tools/list")["result"]["tools"]
        self.assertEqual(len(listed), 7)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in listed))
        response = self.request("tools/call", {"name": "publish", "arguments": {}})
        self.assertTrue(response["result"]["isError"])

    def test_tool_arguments_reject_extra_parameters(self):
        with self.assertRaises(ReadError):
            call(self.engine, "get_task", {"task_id": "x", "command": "delete"})
        with self.assertRaises(ReadError):
            call(self.engine, "search", {"platforms": "weibo", "query": "q"})

    def test_invalid_protocol_envelope(self):
        self.assertEqual(self.protocol.response([])["error"]["code"], -32600)


class WebGuards(unittest.TestCase):
    def fake(self, headers):
        cls = handler_for(None, 8767, "known-token")
        h = object.__new__(cls)
        h.headers = Message()
        for key, value in headers.items():
            h.headers[key] = value
        h.responses = []
        h.send_json = lambda data, status=200: h.responses.append((status, data))
        return h

    def test_local_api_requires_token(self):
        h = self.fake({"Host": "127.0.0.1:8767"})
        self.assertFalse(h.guard(True))
        self.assertEqual(h.responses[0][0], 403)

    def test_cross_origin_and_rebinding_are_rejected(self):
        for headers in [{"Host": "evil.org:8767", "X-Social-Read-Token": "known-token"}, {"Host": "127.0.0.1:8767", "Origin": "https://evil.org", "X-Social-Read-Token": "known-token"}, {"Host": "localhost:8767", "Sec-Fetch-Site": "cross-site", "X-Social-Read-Token": "known-token"}]:
            self.assertFalse(self.fake(headers).guard(True))

    def test_local_same_origin_allowed(self):
        self.assertTrue(self.fake({"Host": "127.0.0.1:8767", "Origin": "http://127.0.0.1:8767", "X-Social-Read-Token": "known-token"}).guard(True))


if __name__ == "__main__":
    unittest.main()


class AdapterContractTests(unittest.TestCase):
    def test_question_reads_full_answers_and_labels_failed_details(self):
        class Fake:
            def __init__(self): self.calls = []
            def call(self, site, cmd, args, cancel):
                self.calls.append((site, cmd, args))
                if cmd == "question":
                    return [{"author":"A", "url":"https://www.zhihu.com/question/10/answer/20", "content":"摘要"}, {"author":"B", "url":"https://www.zhihu.com/question/10/answer/21", "content":"仅有摘要"}]
                if args[0].endswith("/21"):
                    raise ReadError("login_required", "请登录")
                return [{"author":"A", "content":"完整正文" * 120, "url":args[0]}]
        fake = Fake()
        item = OpenCLIAdapter(fake).read("zhihu", "https://www.zhihu.com/question/10", threading.Event())
        self.assertIn("完整正文" * 120, item["body"])
        self.assertEqual(item["answers"][1]["kind"], "excerpt_only")
        self.assertIn("仅摘要", item["body"])
        self.assertEqual(len(item["answer_errors"]), 1)
        self.assertEqual(fake.calls[0][2], ["10", "--limit", "3"])

    def test_live_actions_require_explicit_profile(self):
        with tempfile.TemporaryDirectory() as td:
            e = Engine(td, "live")
            try:
                with self.assertRaises(ReadError) as caught:
                    e.submit("search", {"platforms":["weibo"], "query":"主题"})
                self.assertEqual(caught.exception.code, "profile_required")
            finally: e.shutdown()

    def test_non_string_platform_and_reference_are_safe_errors(self):
        with tempfile.TemporaryDirectory() as td:
            e = Engine(td, "demo")
            try:
                for action, req in [("search", {"platforms":[{}],"query":"主题"}), ("read", {"platform":{},"url":WB}), ("read", {"content_ref":{ "id":"bad" }})]:
                    with self.assertRaises(ReadError): e.submit(action, req)
            finally: e.shutdown()


class ResolverTests(unittest.TestCase):
    def test_resolver_only_closes_its_created_tab_and_returns_wechat_url(self):
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(td, executable="opencli", profile="dedicated")
            calls = []
            def run(args, cancel=None, timeout=None):
                calls.append(args)
                if args[2:4] == ["tab", "new"]: return 0, '{"page":"owned_tab"}', ''
                if args[2:4] == ["get", "url"]: return 0, 'https://mp.weixin.qq.com/s/example\n', ''
                return 0, '{}', ''
            with patch.object(runner, "run", side_effect=run):
                self.assertEqual(runner.resolve_weixin("https://weixin.sogou.com/link?url=PUBLIC"), "https://mp.weixin.qq.com/s/example")
            self.assertEqual(calls[-1][2:], ["tab", "close", "owned_tab"])
            self.assertTrue(all(c[1].startswith("socialread-resolve-") for c in calls))

    def test_resolver_stops_at_challenge_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(td, executable="opencli", profile="dedicated")
            calls = []
            def run(args, cancel=None, timeout=None):
                calls.append(args)
                if args[2:4] == ["tab", "new"]: return 0, '{"page":"owned_tab"}', ''
                if args[2:4] == ["get", "url"]: return 0, 'https://weixin.sogou.com/link?url=PUBLIC', ''
                if args[2:4] == ["get", "text"]: return 0, '{"value":"请输入验证码"}', ''
                return 0, '{}', ''
            with patch.object(runner, "run", side_effect=run), self.assertRaises(ReadError) as exc:
                runner.resolve_weixin("https://weixin.sogou.com/link?url=PUBLIC")
            self.assertEqual(exc.exception.code, "verification_required")
            self.assertEqual(calls[-1][2:], ["tab", "close", "owned_tab"])

    def test_upstream_error_row_is_not_accepted_as_article(self):
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(td, executable="opencli")
            with patch.object(runner, "run", return_value=(0,'[{"title":"Error","status":"failed — verification required"}]','')), self.assertRaises(ReadError) as exc:
                runner.call("weixin", "download", [])
            self.assertEqual(exc.exception.code, "verification_required")


class DateEvidenceTests(unittest.TestCase):
    def test_id_date_estimate_is_not_official_publish_date(self):
        item = search_item("xiaohongshu", {"title":"标题", "url":XHS, "published_at":"2026-01-01"}, "主题", "opencli")
        self.assertIsNone(item["published_at_raw"])
        self.assertEqual(item["date_estimate"], "2026-01-01")
        self.assertEqual(item["date_estimate_method"], "note_id_timestamp")


class ReadFailureTests(unittest.TestCase):
    def test_upstream_empty_on_read_is_not_search_empty(self):
        class Unavailable(DemoAdapter):
            def read(self, *args): raise ReadError("empty", "本次搜索没有结果。")
        with tempfile.TemporaryDirectory() as td:
            e = Engine(td, "demo", adapter=Unavailable())
            try:
                task = e.wait(e.submit("read", {"platform":"weibo", "url":WB})["task_id"], 3)
                self.assertEqual(task["status"], "failed")
                self.assertEqual(task["result"]["errors"][0]["status"], "content_unavailable")
                self.assertNotIn("搜索", task["result"]["errors"][0]["message"])
            finally: e.shutdown()


class PublicArticleTests(unittest.TestCase):
    def test_extracts_scoped_body_and_ordered_images_without_scripts(self):
        from social_read.weixin_public import ArticleParser
        raw = '<h2 id="activity-name">标题</h2><a id="js_name">公众号</a><div id="js_content"><p>第一段<br>第二行</p><script>secret</script><img data-src="https://mmbiz.qpic.cn/image/a"><img src="https://evil.org/a"></div><footer>噪声</footer>'
        p = ArticleParser(); p.feed(raw); item = p.article(raw)
        self.assertEqual(item["title"], "标题")
        self.assertNotIn("secret", item["content"]); self.assertNotIn("噪声", item["content"])
        self.assertEqual(len(item["media"]), 1);self.assertEqual(item["media"][0]["order"], 1)
    def test_challenge_is_never_article_text(self):
        from social_read.weixin_public import ArticleParser
        raw='<h2 id="activity-name">环境异常</h2><div id="js_content">去验证</div>'
        p=ArticleParser();p.feed(raw)
        with self.assertRaises(ReadError) as exc:p.article(raw)
        self.assertEqual(exc.exception.code,"verification_required")
    def test_redirect_outside_article_hosts_is_rejected(self):
        from social_read.weixin_public import SafeRedirect
        with self.assertRaises(ReadError):SafeRedirect().redirect_request(None,None,302,'',{},'https://evil.org/s/a')


class URLNormalizationTests(unittest.TestCase):
    def test_unicode_redirect_query_is_http_safe_without_double_encoding(self):
        uri=validate_url("weixin", "https://mp.weixin.qq.com/s?keyword=知识&sn=a%2Fb")
        self.assertNotIn("知识",uri)
        self.assertIn("%E7%9F%A5%E8%AF%86",uri)
        self.assertIn("sn=a%2Fb",uri)


class NavigationEvidenceTests(unittest.TestCase):
    def test_navigation_failure_is_distinct_from_offline_bridge(self):
        err = classify_error("Pre-navigation failed: Navigation rejected. Check browser extension is running.")
        self.assertEqual(err.code, "navigation_rejected")
    def test_weibo_search_uses_excerpt_not_long_body_as_title(self):
        item = search_item("weibo", {"title":"文字"*200, "url":WB}, "主题", "opencli")
        self.assertLessEqual(len(item["title"]), 101)
        self.assertTrue(item["title_is_excerpt"])


class PartialAnswerTests(unittest.TestCase):
    def test_mixed_answer_collection_keeps_content_and_reports_partial(self):
        class Mixed(DemoAdapter):
            def read(self, site, target, cancel):
                d=super().read(site,target,cancel)
                d["answer_errors"]=[{"status":"navigation_rejected","message":"浏览器跳转失败"}]
                return d
        with tempfile.TemporaryDirectory() as td:
            e=Engine(td,"demo",adapter=Mixed())
            try:
                j=e.wait(e.submit("read",{"platform":"zhihu","url":"https://www.zhihu.com/question/10"})["task_id"],3)
                self.assertEqual(j["status"],"partial");self.assertTrue(j["result"]["content"]["body"])
                cached=e.wait(e.submit("read",{"content_ref":j["result"]["content"]["content_ref"]})["task_id"],3)
                self.assertEqual(cached["status"],"partial")
            finally:e.shutdown()


class SogouClickTests(unittest.TestCase):
    def test_link_gets_observed_normal_click_parameters_once(self):
        with tempfile.TemporaryDirectory() as td:
            r=Runner(td,executable="opencli",profile="dedicated");calls=[]
            target="https://weixin.sogou.com/link?url=" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"*20
            def run(args,*a,**kw):
                calls.append(args)
                if args[2:4]==["tab","new"]:return 0,'{"page":"own"}',''
                if args[2:4]==["get","url"]:return 0,'https://mp.weixin.qq.com/s/article',''
                return 0,'{}',''
            with patch("social_read.adapter.secrets.randbelow",return_value=9),patch.object(r,"run",side_effect=run):r.resolve_weixin(target)
            expected=target[target.index("url=")+4+21+10]
            self.assertEqual(calls[0][-1],target+"&k=10&h="+expected)
    def test_parameter_error_is_not_an_empty_article(self):
        from social_read.weixin_public import ArticleParser
        raw='<body>参数错误</body>';p=ArticleParser();p.feed(raw)
        with self.assertRaises(ReadError) as exc:p.article(raw)
        self.assertEqual(exc.exception.code,"source_resolution_required")


class SogouIndexTests(unittest.TestCase):
    def test_scoped_cards_keep_titles_dates_and_ignore_scripts(self):
        from social_read.weixin_public import SearchParser
        raw="""<ul><li id="sogou_vr_11002601_box_0"><h3><a href="/link?url=PUBLIC">知识<em>管理</em></a></h3><p class="txt-info">摘要</p><span class="s2"><script>document.write(timeConvert('1566126978'))</script></span></li></ul>"""
        p=SearchParser();p.feed(raw)
        self.assertEqual(p.rows[0]["title"],"知识管理")
        self.assertTrue(p.rows[0]["publish_time"].startswith("2019-08-18"))
        self.assertEqual(p.rows[0]["summary"],"摘要")


class SourceAccessTests(unittest.TestCase):
    def test_wechat_signature_removed_from_all_public_shapes(self):
        url = "https://mp.weixin.qq.com/s?src=11&signature=PRIVATE_WECHAT_SIGNATURE&new=1"
        safe = scrub({"signature":"PRIVATE_WECHAT_SIGNATURE", "url":url, "text":"signature=PRIVATE_WECHAT_SIGNATURE"})
        self.assertNotIn("PRIVATE_WECHAT_SIGNATURE", json.dumps(safe))
        self.assertIn("src=11", safe["url"])

    def test_local_source_keeps_access_parameters_and_rejects_extra_fields(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            store = Store(td)
            item = store.save_item("xiaohongshu", search_item("xiaohongshu", {"title":"标题", "url":XHS}, "主题", "test"))
            cls = handler_for(SimpleNamespace(mode="live",store=store),8767,"known-token")
            def post(data, token="known-token", origin="http://127.0.0.1:8767"):
                h = object.__new__(cls);h.path="/api/source";h.headers=Message()
                raw=json.dumps(data).encode()
                for key,value in {"Host":"127.0.0.1:8767","Origin":origin,"X-Social-Read-Token":token,"Content-Type":"application/json","Content-Length":str(len(raw))}.items():h.headers[key]=value
                h.rfile=io.BytesIO(raw);h.responses=[]
                h.send_json=lambda data,status=200:h.responses.append((status,data))
                h.do_POST();return h.responses[0]
            self.assertEqual(post({"content_ref":item["content_ref"]}), (200,{"url":XHS}))
            self.assertEqual(post({"content_ref":item["content_ref"]},token="")[0],403)
            self.assertEqual(post({"content_ref":item["content_ref"]},origin="https://evil.org")[0],403)
            self.assertEqual(post({"content_ref":item["content_ref"],"url":"https://evil.org"})[0],400)
            self.assertEqual(post({"content_ref":{}})[0],400)
            self.assertNotIn("PRIVATE_SIGNATURE",json.dumps(store.item(item["content_ref"])["data"]))
            store.close()


class WechatAccessFallbackTests(unittest.TestCase):
    def test_public_parameter_error_can_use_authorized_browser_export(self):
        class Fake:
            def __init__(self, root):self.workdir=Path(root);self.calls=[]
            def call(self, site, command, args, cancel):
                self.calls.append((site,command))
                output=Path(args[args.index("--output")+1]);(output/"article.md").write_text("浏览器可见正文")
                return [{"title":"文章标题","status":"success"}]
        with tempfile.TemporaryDirectory() as td:
            runner=Fake(td)
            with patch("social_read.weixin_public.read_public_article",side_effect=ReadError("source_resolution_required","参数错误")):
                item=OpenCLIAdapter(runner).read("weixin","https://mp.weixin.qq.com/s/fixture",threading.Event())
            self.assertEqual(item["body"],"浏览器可见正文")
            self.assertEqual(runner.calls,[("weixin","download")])

    def test_verification_stops_before_browser_fallback(self):
        runner=object()
        with patch("social_read.weixin_public.read_public_article",side_effect=ReadError("verification_required","去验证")),self.assertRaises(ReadError) as exc:
            OpenCLIAdapter(runner).read("weixin","https://mp.weixin.qq.com/s/fixture",threading.Event())
        self.assertEqual(exc.exception.code,"verification_required")
