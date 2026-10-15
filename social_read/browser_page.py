"""Owned, profile-pinned pages for fixed internal read adapters."""
import json
import os
from pathlib import Path
import re
import uuid
import time
from urllib.parse import urlsplit
from .model import ReadError, classify_error

class ReadPage:
    def __init__(self, runner, url, cancel, label="content"):
        self.runner, self.url, self.cancel = runner, url, cancel
        self.session = "smrfa-" + label + "-" + uuid.uuid4().hex
        self.tab = None
        self.blank_navigation_grace = None
        self.foreground = False

    def command(self, args, timeout=20):
        try:
            code, out, err = self.runner.run(["browser", self.session] + args, self.cancel, timeout)
            if code:
                raise classify_error(out + err, code)
            return out.strip()
        except ReadError as error:
            error.stage = "create_page" if args[:2]==["tab","new"] else "page_extract"
            raise

    def json(self, args, timeout=20):
        try:
            value = json.JSONDecoder().raw_decode(self.command(args, timeout))[0]
        except (ValueError, TypeError):
            raise ReadError("adapter_incompatible", "浏览器读取结果格式发生变化。")
        return value

    def evaluate(self, script, timeout=20):
        return self.json(["eval", script, "--tab", self.tab], timeout)

    def wait(self, selector, seconds=12):
        try:
            wait_selector(self.runner, self.session, self.tab, selector, self.cancel, seconds)
        except ReadError as error:
            error.stage = "content_ready"
            raise

    def check(self):
        data = self.evaluate("({value:document.body?.innerText || ''})")
        text = str(data.get("value", "")) if isinstance(data, dict) else str(data)
        if re.search(r"验证码|安全验证|安全限制|访问链接异常|异常访问|去验证", text):
            raise ReadError("verification_required", "平台页面要求验证，读取已停止。")
        if re.search(r"登录后查看|请登录后|登录后阅读全文", text):
            raise ReadError("login_required", "请在专用浏览器资料中登录后重新读取。")

    def __enter__(self):
        if not self.runner.profile:
            raise ReadError("profile_required", "读取需要明确指定专用浏览器资料。")
        opened = self.json(["tab", "new", self.url]+(["--window","foreground"] if self.foreground else []), 25)
        self.tab = opened.get("page") if isinstance(opened, dict) else None
        if not isinstance(self.tab, str) or not re.fullmatch(r"[\w-]{1,100}", self.tab):
            self.tab = None
            raise ReadError("adapter_incompatible", "浏览器没有返回本次创建的页面标识。")
        try:
            self.navigation = wait_navigation(self.runner, self.session, self.tab, self.cancel, 25, self.blank_navigation_grace)
            self.wait("body", 16)
        except Exception as error:
            if isinstance(error, ReadError):error.stage = "page_ready"
            self.__exit__()
            raise
        return self

    def __exit__(self, *args):
        if self.tab:
            try:
                self.runner.run(["browser", self.session, "tab", "close", self.tab], timeout=5)
            except ReadError:
                pass


def _pause(cancel, seconds):
    if cancel:
        if cancel.wait(seconds):
            raise ReadError("cancelled", "任务已取消。")
    else:
        time.sleep(seconds)


def wait_navigation(runner, session, tab, cancel, seconds=25, blank_grace=None):
    """Do not attach an evaluator to the initial empty document of a new tab.

    OpenCLI tab creation returns before Chrome commits its navigation. Native
    tab metadata can be read without injecting a wait into that old document.
    Only inspect this operation's explicitly owned target, never another tab.
    """
    deadline = time.monotonic() + seconds
    started=time.monotonic();samples=[]
    while time.monotonic() < deadline:
        if cancel and cancel.is_set():
            raise ReadError("cancelled", "任务已取消。")
        code, out, err = runner.run(["browser", session, "tab", "list"], cancel, max(.1, deadline-time.monotonic()))
        if code:
            raise classify_error(out+err, code)
        try:
            tabs = json.JSONDecoder().raw_decode(out.strip())[0]
            if not isinstance(tabs, list):
                raise ReadError("adapter_incompatible", "浏览器导航状态格式发生变化。")
            owned = next((t for t in tabs if isinstance(t, dict) and t.get("page") == tab), None)
            url = urlsplit(owned.get("url") or "") if owned else None
        except (ValueError, TypeError, AttributeError):
            raise ReadError("adapter_incompatible", "浏览器导航状态格式发生变化。")
        sample={"found":bool(owned),"scheme":url.scheme if url else None,"host":url.hostname if url else None}
        if not samples or samples[-1]!=sample:samples.append(sample)
        if url and url.scheme in {"http", "https"} and url.hostname:
            receipt={"status":"ready","seconds":round(time.monotonic()-started,3),"transitions":samples}
            _navigation_receipt(runner,receipt)
            return {k:v for k,v in receipt.items() if k!='transitions'}
        if blank_grace is not None and owned and url and url.scheme in {"", "about"} and time.monotonic()-started >= blank_grace:
            _navigation_receipt(runner,{"status":"navigation_not_started","seconds":round(time.monotonic()-started,3),"transitions":samples})
            raise ReadError("navigation_not_started","本次任务页仍为空白，未开始平台导航；将关闭它并进行一次有界的新页面恢复。")
        _pause(cancel, min(.4, max(0, deadline-time.monotonic())))
    _navigation_receipt(runner,{"status":"navigation_loading_timeout","seconds":round(time.monotonic()-started,3),"transitions":samples})
    raise ReadError("navigation_loading_timeout", "本次新页面未在预算内完成导航，已关闭本次页面；这不表示账号被封或没有搜索结果。")


def wait_selector(runner, session, tab, selector, cancel, seconds=12):
    # Probe synchronously from the host: an in-page timer can be throttled in
    # background tabs or invalidated when a normal article click navigates.
    script = """(()=>{
      const selector=%s;
      const text=document.body?.innerText || '';
      return {ready:/^https?:$/.test(location.protocol) && !!document.querySelector(selector),
        verification:/验证码|安全验证|安全限制|访问链接异常|去验证/.test(text),
        login:/登录后查看|请登录后/.test(text),
        rate_limited:/访问过于频繁|请求过于频繁/.test(text)};
    })()""" % json.dumps(selector)
    # The bridge itself allows 8 seconds to connect. A separate 6-second
    # subprocess cap used to kill valid first probes before that deadline.
    # Share the remaining host budget; synchronous probes normally return fast.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancel and cancel.is_set():
            raise ReadError("cancelled", "任务已取消。")
        code, out, err = runner.run(["browser", session, "eval", script, "--tab", tab], cancel, max(.1, deadline-time.monotonic()))
        if code:
            # A normal page transition may destroy the old execution context.
            # No navigation/click is replayed; unknown outcomes are not retried.
            if re.search(r"execution context was destroyed|cannot find context|target_navigated|inspected target navigated", out+err, re.I) and not re.search(r"command_result_unknown|command_lost|result_evicted", out+err):
                _pause(cancel, min(.4, max(0, deadline-time.monotonic())))
                continue
            raise classify_error(out+err, code)
        try:
            data=json.JSONDecoder().raw_decode(out.strip())[0]
        except ValueError:
            raise ReadError("adapter_incompatible", "页面就绪检查格式发生变化。")
        if not isinstance(data, dict):
            raise ReadError("adapter_incompatible", "页面就绪检查格式发生变化。")
        if data.get("ready"):
            return
        for flag, message in (("verification", "平台要求验证，读取已停止。"), ("login", "平台要求登录，请在专用浏览器资料中完成。"), ("rate_limited", "平台限制了本次请求，读取已停止。")):
            if data.get(flag):
                status={"verification":"verification_required", "login":"login_required", "rate_limited":"rate_limited"}[flag]
                raise ReadError(status, message)
        _pause(cancel, min(.4, max(0, deadline-time.monotonic())))
    raise ReadError("content_loading_timeout", "页面内容未在等待时间内出现，已停止读取；这不表示内容已删除。")


def _navigation_receipt(runner,receipt):
    """Private timing/state only: no queries, URL tokens or account IDs."""
    root=getattr(runner,"workdir",None)
    if not isinstance(root,(str,os.PathLike)):return
    try:
        fd=os.open(str(Path(root)/"navigation-diagnostics.jsonl"),os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
        try:os.write(fd,(json.dumps({"at":time.time(),**receipt})+"\n").encode())
        finally:os.close(fd)
    except OSError:pass
