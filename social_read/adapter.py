import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .model import PLATFORMS, ReadError, classify_error, content_item, rows, search_item, validate_url

# Business-action allowlist. Never expose arbitrary CLI arguments or browser eval.
READ_COMMANDS = {
    "xiaohongshu": {"search", "note", "comments", "whoami"},
    "weibo": {"search", "post", "comments", "whoami"},
    "zhihu": {"search", "question", "answer-detail", "answer-comments", "download", "whoami"},
    "weixin": {"search", "download"},
}


def parse_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise ReadError("adapter_incompatible", "读取组件没有返回有效的 JSON。请检查组件版本。")


class Runner:
    def __init__(self, workdir, executable=None, profile=None, timeout=60):
        self.workdir = Path(workdir)
        from .runtime import opencli_executable
        self.executable = executable or opencli_executable()
        self.profile = profile
        self.timeout = timeout
        if profile and (not re.fullmatch(r"[\w .-]{1,80}", profile) or profile.startswith("-")):
            raise ReadError("invalid_input", "浏览器资料名称格式无效。")

    def run(self, args, cancel=None, timeout=None):
        if not self.executable:
            raise ReadError("adapter_missing", "请先安装 OpenCLI，再连接浏览器扩展。")
        from .runtime import command_env
        env = command_env()
        env["OPENCLI_BROWSER_CONNECT_TIMEOUT"] = "8"
        env["OPENCLI_BROWSER_COMMAND_TIMEOUT"] = str(self.timeout)
        if self.profile:
            env["OPENCLI_PROFILE"] = self.profile
        else:
            env.pop("OPENCLI_PROFILE", None)
        env["OPENCLI_WINDOW"] = "background"
        p = None
        with tempfile.TemporaryFile(mode="w+b", dir=str(self.workdir)) as out, tempfile.TemporaryFile(mode="w+b", dir=str(self.workdir)) as err:
            try:
                p = subprocess.Popen([self.executable] + args, stdout=out, stderr=err, cwd=str(self.workdir), env=env, start_new_session=True)
                deadline = time.monotonic() + (timeout or self.timeout)
                while p.poll() is None:
                    if cancel and cancel.is_set():
                        raise ReadError("cancelled", "任务已取消，已完成的内容保留。")
                    if time.monotonic() > deadline:
                        raise ReadError("timeout", "本次读取超时，已停止执行。", True)
                    if out.tell() > 4 * 1024 * 1024 or err.tell() > 1024 * 1024:
                        raise ReadError("adapter_incompatible", "组件输出超出读取预算，任务已停止。")
                    time.sleep(0.08)
                out.seek(0); err.seek(0)
                return p.returncode, out.read(4 * 1024 * 1024).decode("utf-8", "replace"), err.read(1024 * 1024).decode("utf-8", "replace")
            except OSError as exc:
                raise classify_error(str(exc))
            finally:
                if p and p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.wait(timeout=2)
                    except ProcessLookupError:
                        pass

    def call(self, site, command, args, cancel=None):
        if command not in READ_COMMANDS.get(site, set()):
            raise ReadError("forbidden_action", "首版只允许已登记的读取操作。")
        # Reuse only the explicitly selected profile's platform container.
        cli_args = [site, command] + args + ["--site-session", "persistent", "-f", "json"]
        for attempt in (1, 2):
            code, out, err = self.run(cli_args, cancel)
            error = classify_error(out + "\n" + err, code) if code else None
            record = {"at": datetime.now(timezone.utc).isoformat(), "platform": site, "command": command, "attempt": attempt, "session": "persistent", "status": error.code if error else "success"}
            fd = os.open(str(self.workdir / "adapter-attempts.jsonl"), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            try:
                os.write(fd, (json.dumps(record) + "\n").encode())
            finally:
                os.close(fd)
            if not error:
                break
            if error.code != "navigation_rejected" or attempt == 2:
                raise error
            if cancel:
                if cancel.wait(2):
                    raise ReadError("cancelled", "任务已取消。")
            else:
                time.sleep(2)
        data = parse_json(out)
        if isinstance(data, dict) and (data.get("error") or data.get("ok") is False):
            raise classify_error(json.dumps(data, ensure_ascii=False))
        for row in rows(data):
            if isinstance(row, dict) and (row.get("error") or str(row.get("status", "")).lower().startswith(("failed", "error", "invalid"))):
                raise classify_error(json.dumps(row, ensure_ascii=False))
        return data

    def resolve_weixin(self, target, cancel=None):
        """Private, fixed navigation to a validated index link. No agent browser commands."""
        target = validate_url("weixin", target)
        if urlsplit(target).hostname != "weixin.sogou.com" or not self.profile:
            raise ReadError("invalid_input", "解析需要搜狗索引链接和专用浏览器资料。")
        # Sogou's public result-page click handler adds k and h to each article link.
        # Observed 2026-10-14: href.substr(indexOf("url=") + 4 + 21 + k, 1).
        # These are normal navigation parameters, not a verification challenge.
        if "&k=" not in target:
            k = secrets.randbelow(100) + 1
            at = target.find("url=")
            if at < 0:
                raise ReadError("source_resolution_required", "搜狗索引链接缺少正常跳转参数。")
            h = target[at + 4 + 21 + k:at + 4 + 21 + k + 1]
            target += "&k=%s&h=%s" % (k, h)
        session = "socialread-resolve-" + uuid.uuid4().hex
        tab = None
        try:
            code, out, err = self.run(["browser", session, "tab", "new", target], cancel, 20)
            if code:
                raise classify_error(out + err, code)
            opened = json.JSONDecoder().raw_decode(out.strip())[0]
            tab = opened.get("page")
            if not isinstance(tab, str) or not re.fullmatch(r"[\w-]{1,100}", tab):
                raise ReadError("adapter_incompatible", "解析组件没有返回独立页面标识。")
            for _ in range(3):
                if cancel and cancel.wait(0.5):
                    raise ReadError("cancelled", "任务已取消。")
                code, out, err = self.run(["browser", session, "get", "url", "--tab", tab], cancel, 10)
                if code:
                    raise classify_error(out + err, code)
                current = out.strip().splitlines()[0]
                parsed = urlsplit(current)
                if parsed.hostname == "mp.weixin.qq.com":
                    return validate_url("weixin", current, allow_index=False)
                if parsed.hostname != "weixin.sogou.com":
                    raise ReadError("source_resolution_required", "搜狗结果没有跳转到受支持的微信文章地址。")
            code, out, err = self.run(["browser", session, "get", "text", "body", "--tab", tab], cancel, 10)
            if code:
                raise classify_error(out + err, code)
            text = json.JSONDecoder().raw_decode(out.strip())[0].get("value", "")
            if re.search(r"验证码|安全验证|异常访问|频繁|去验证", str(text)):
                raise classify_error(text)
            raise ReadError("source_resolution_required", "没有取得微信原文地址。请打开原始搜索结果，复制微信文章链接后阅读。")
        except (ValueError, TypeError, AttributeError):
            raise ReadError("adapter_incompatible", "解析组件返回格式发生变化。")
        finally:
            if tab and re.fullmatch(r"[\w-]{1,100}", tab):
                try:
                    self.run(["browser", session, "tab", "close", tab], timeout=5)
                except ReadError:
                    pass

    def diagnose(self, cancel=None):
        if not self.executable:
            return {"status": "adapter_missing", "message": "OpenCLI 未安装。"}
        code, version, _ = self.run(["--version"], cancel, 5)
        if code:
            raise ReadError("adapter_missing", "无法读取 OpenCLI 版本。")
        code, output, error = self.run(["doctor"], cancel, 20)
        if "[FAIL]" in output or "[MISSING]" in output or code:
            e = classify_error(output + error)
            result = e.result()
            result["version"] = version.strip()
            result["message"] = "浏览器连接未就绪，请在本机运行启动器并检查扩展。" if e.code == "adapter_error" else e.message
            return result
        return {"status": "connected", "message": "浏览器桥接检查通过；平台登录仍需逐项核查。", "version": version.strip()}


class OpenCLIAdapter:
    name = "opencli"

    def __init__(self, runner):
        self.runner = runner

    def search(self, site, query, limit, cancel):
        if site == "xiaohongshu":
            from .xiaohongshu_search import search_notes
            return search_notes(self.runner, query, limit, cancel)
        if site == "zhihu":
            from .zhihu_search import search_zhihu
            return search_zhihu(self.runner, query, limit, cancel)
        if site == "weixin":
            from .weixin_public import search_public_articles
            try:
                data = search_public_articles(query, limit, cancel)
                return [search_item(site, r, query, "public-http-sogou") for r in data]
            except ReadError as exc:
                if exc.code != "public_index_unavailable":
                    raise
        data = self.runner.call(site, "search", [query, "--limit", str(limit)], cancel)
        return [search_item(site, r, query, self.name) for r in rows(data)]

    def read_with_progress(self, site, target, cancel, context, progress):
        if site == "xiaohongshu":
            from .xiaohongshu_read import read_note
            return read_note(self.runner, target, cancel, context=context, progress=progress)
        return self.read_with_context(site, target, cancel, context)

    def read_with_context(self, site, target, cancel, context):
        if site == "weixin" and urlsplit(target).hostname == "weixin.sogou.com" and context.get("query"):
            from .weixin_browser import read_search_article
            item = read_search_article(self.runner, context["query"], context.get("title"), cancel)
            item["search_source_url"] = target
            return item
        return self.read(site, target, cancel)

    def read(self, site, target, cancel):
        target = validate_url(site, target)
        p = urlsplit(target)
        if site == "xiaohongshu":
            if not parse_qs(p.query).get("xsec_token"):
                raise ReadError("access_context_required", "请从搜索结果打开笔记，或粘贴包含访问参数的完整小红书链接。")
            from .xiaohongshu_read import read_note
            return read_note(self.runner, target, cancel)
        if site == "weibo":
            from .platform_reads import read_weibo
            return read_weibo(self.runner, target, cancel)
        if site == "zhihu" and "/answer/" in p.path:
            from .platform_reads import read_zhihu_answer
            return read_zhihu_answer(self.runner, target, cancel)
        if site == "zhihu" and "/question/" in p.path:
            data = self.runner.call(site, "question", [p.path.rstrip("/").split("/")[-1], "--limit", "3"], cancel)
            answers = rows(data)
            if not answers:
                raise ReadError("content_unavailable", "问题页没有返回可见回答。")
            from .model import strip_markup
            detailed, failures = [], []
            for a in answers[:3]:
                answer_url = a.get("url", "")
                try:
                    validated = validate_url("zhihu", answer_url)
                    if not urlsplit(validated).path.startswith(p.path.rstrip("/") + "/answer/"):
                        raise ReadError("adapter_incompatible", "回答来源与问题不一致。")
                    detail = content_item(site, self.runner.call(site, "answer-detail", [validated], cancel), validated, self.name)
                    detailed.append({"author": detail.get("author") or a.get("author"), "url": validated, "body": detail["body"], "kind": "read_answer"})
                except ReadError as exc:
                    if exc.code == "cancelled":
                        raise
                    failures.append({"url": answer_url, **exc.result()})
                    detailed.append({"author": a.get("author"), "url": answer_url, "body": strip_markup(a.get("content")), "kind": "excerpt_only"})
            body = "\n\n".join("回答 %s · %s%s\n%s" % (i + 1, a.get("author") or "作者未提供", "（仅摘要）" if a["kind"] == "excerpt_only" else "", a["body"]) for i, a in enumerate(detailed))
            item = content_item(site, {"title": "问题下的部分回答", "content": body, "url": target}, target, self.name)
            item["answers"] = detailed
            item["answer_errors"] = failures
            item["type"] = "answer_collection"
            item["notice"] = "本次选择 %s 个回答分别读取，不代表全部回答。%s完整性需对照原帖。" % (len(detailed), "其中 %s 个只能取得摘要。" % len(failures) if failures else "")
            return item
        if site == "zhihu" and p.path.startswith("/p/"):
            from .platform_reads import read_zhihu_article
            return read_zhihu_article(self.runner, target, cancel)
        index_url = None
        if site == "weixin" and p.hostname == "weixin.sogou.com":
            index_url = target
            target = self.runner.resolve_weixin(target, cancel)
        if site == "weixin":
            from .weixin_public import read_public_article, article_item
            try:
                article = read_public_article(target, cancel)
                item = article_item(article, target, "public-http")
                if index_url:
                    item["search_source_url"] = index_url
                return item
            except ReadError as exc:
                if exc.code not in {"public_article_unavailable", "source_resolution_required"}:
                    raise
                if getattr(self.runner, "profile", None):
                    from .weixin_browser import read_article
                    item = read_article(self.runner, target, cancel)
                    if index_url:
                        item["search_source_url"] = index_url
                    return item
        with tempfile.TemporaryDirectory(prefix="article-", dir=str(self.runner.workdir)) as temp:
            raw = self.runner.call(site, "download", ["--url", target, "--output", temp, "--download-images", "false"], cancel)
            files = list(Path(temp).rglob("*.md"))
            if len(files) != 1:
                raise ReadError("adapter_incompatible", "文章导出没有产生唯一的正文文件。")
            f = files[0]
            if f.is_symlink() or not f.resolve().is_relative_to(Path(temp).resolve()) or f.stat().st_size > 2 * 1024 * 1024:
                raise ReadError("adapter_incompatible", "文章文件格式或大小不符合读取要求。")
            item = content_item(site, raw, target, self.name, f.read_text(encoding="utf-8"))
            if index_url:
                item["search_source_url"] = index_url
            return item

    def comments(self, site, target, limit, cancel, replies_limit=3, cursor=None):
        if site == "weixin":
            raise ReadError("unsupported", "公众号留言读取尚未支持。")
        if site == "xiaohongshu" and not parse_qs(urlsplit(target).query).get("xsec_token"):
            raise ReadError("access_context_required", "请从搜索结果打开小红书评论。")
        from .comments import read_comments
        return read_comments(self.runner, site, target, limit, replies_limit, cancel, cursor)


class DemoAdapter:
    name = "fictional-demo"

    def search(self, site, query, limit, cancel):
        target = {"xiaohongshu": "https://www.xiaohongshu.com/explore/000000000000000000000001?xsec_token=DEMO_ONLY", "weibo": "https://weibo.com/1000000000/AbCdDemo", "zhihu": "https://www.zhihu.com/question/100000000/answer/200000000", "weixin": "https://mp.weixin.qq.com/s/FICTIONAL_DEMO"}[site]
        return [search_item(site, {"title": "虚构样例｜%s的使用反馈" % query, "author": "演示作者", "url": target, "type": "demo", "summary": "仅演示读取流程，不是平台真实内容。"}, query, self.name)]

    def read(self, site, target, cancel):
        validate_url(site, target)
        item = content_item(site, {"title": "虚构样例｜个人知识管理工具", "author": "演示作者", "content": "这是离线演示资料，不是社交平台真实帖子。\n\n样例观点：用户希望搜索方便、来源可回查，也希望退出后能继续上次阅读。\n\n这段文字只用于检查界面、引用与任务记录。", "url": target}, target, self.name)
        item["notice"] = "离线演示，内容完全虚构；不代表真实平台读取成功。"
        return item

    def comments(self, site, target, limit, cancel, replies_limit=3, cursor=None):
        if site == "weixin":
            raise ReadError("unsupported", "公众号留言暂不支持。")
        validate_url(site, target)
        return {"items": [{"author": "虚构用户", "text": "这是一条演示评论，不来自真实平台。"}], "coverage": {"returned_rows": 1, "all_comments": False}, "notice": "评论完全虚构，未连接平台。"}
