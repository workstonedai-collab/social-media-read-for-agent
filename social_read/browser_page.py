"""Owned, profile-pinned pages for fixed internal read adapters."""
import json
import re
import uuid
from .model import ReadError, classify_error

class ReadPage:
    def __init__(self, runner, url, cancel, label="content"):
        self.runner, self.url, self.cancel = runner, url, cancel
        self.session = "smrfa-" + label + "-" + uuid.uuid4().hex
        self.tab = None

    def command(self, args, timeout=20):
        code, out, err = self.runner.run(["browser", self.session] + args, self.cancel, timeout)
        if code:
            raise classify_error(out + err, code)
        return out.strip()

    def json(self, args, timeout=20):
        try:
            value = json.JSONDecoder().raw_decode(self.command(args, timeout))[0]
        except (ValueError, TypeError):
            raise ReadError("adapter_incompatible", "浏览器读取结果格式发生变化。")
        return value

    def evaluate(self, script, timeout=20):
        return self.json(["eval", script, "--tab", self.tab], timeout)

    def wait(self, selector, seconds=12):
        wait_selector(self.runner, self.session, self.tab, selector, self.cancel, seconds)

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
        opened = self.json(["tab", "new", self.url], 25)
        self.tab = opened.get("page") if isinstance(opened, dict) else None
        if not isinstance(self.tab, str) or not re.fullmatch(r"[\w-]{1,100}", self.tab):
            self.tab = None
            raise ReadError("adapter_incompatible", "浏览器没有返回本次创建的页面标识。")
        try:
            self.wait("body", 10)
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *args):
        if self.tab:
            try:
                self.runner.run(["browser", self.session, "tab", "close", self.tab], timeout=5)
            except ReadError:
                pass


def wait_selector(runner, session, tab, selector, cancel, seconds=12):
    # OpenCLI wait uses MutationObserver(document.body), which races a new
    # document before body exists. This bounded loop also handles that phase.
    script = """(async()=>{
      const selector=%s, deadline=Date.now()+%s;
      while(Date.now()<deadline){
        if(document.querySelector(selector))return {ready:true};
        await new Promise(resolve=>setTimeout(resolve,250));
      }
      const text=document.body?.innerText || '';
      return {ready:false, verification:/验证码|安全验证|安全限制|访问链接异常|去验证/.test(text),
        login:/登录后查看|请登录后/.test(text)};
    })()""" % (json.dumps(selector), int(seconds * 1000))
    code, out, err = runner.run(["browser", session, "eval", script, "--tab", tab], cancel, seconds+5)
    if code:
        raise classify_error(out+err, code)
    try:
        data=json.JSONDecoder().raw_decode(out.strip())[0]
    except ValueError:
        raise ReadError("adapter_incompatible", "页面就绪检查格式发生变化。")
    if data.get("verification"):
        raise ReadError("verification_required", "平台要求验证，读取已停止。")
    if data.get("login"):
        raise ReadError("login_required", "平台要求登录，请在专用浏览器资料中完成。")
    if not data.get("ready"):
        raise ReadError("content_loading_timeout", "页面内容未在等待时间内出现，已停止读取；这不表示内容已删除。")
