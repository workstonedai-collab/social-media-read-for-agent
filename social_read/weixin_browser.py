"""Normal Sogou article click in one owned tab, pinned to the chosen profile."""
import json
import re
import uuid
from urllib.parse import urlencode

from .model import ReadError, classify_error, content_item, keyword, validate_url
from .weixin_public import ArticleParser
from .browser_page import wait_selector


def read_search_article(runner, query, title, cancel):
    if not runner.profile or not isinstance(title, str) or not title or len(title) > 1000:
        raise ReadError("source_resolution_required", "需要搜索记录和专用浏览器资料才能打开原文。")
    query = keyword(query)
    session = "socialread-article-" + uuid.uuid4().hex
    tab = None

    def command(args, timeout=15):
        code, out, err = runner.run(["browser", session] + args, cancel, timeout)
        if code:
            raise classify_error(out + err, code)
        return out.strip()

    def page_text():
        return json.JSONDecoder().raw_decode(command(["get", "text", "body", "--tab", tab]))[0].get("value", "")

    def check_page():
        text = str(page_text())
        if re.search(r"验证码|安全验证|环境异常.*验证|异常访问|去验证", text):
            raise ReadError("verification_required", "平台页面要求验证，读取已停止。请在专用浏览器完成后重试。")
        if "参数错误" in text:
            raise ReadError("source_resolution_required", "正常点击后微信仍未接受文章地址，请到原文核对。")

    try:
        url = "https://weixin.sogou.com/weixin?" + urlencode({"query": query, "type": 2, "page": 1, "ie": "utf8"})
        opened = json.JSONDecoder().raw_decode(command(["tab", "new", url], 20))[0]
        tab = opened.get("page")
        if not isinstance(tab, str) or not re.fullmatch(r"[\w-]{1,100}", tab):
            raise ReadError("adapter_incompatible", "浏览器没有返回本次创建的页面标识。")
        try:
            wait_selector(runner, session, tab, ".news-list h3 a", cancel, 15)
        except ReadError:
            check_page()
            raise
        # The title is a JSON string, never executable source. Only a known result
        # link gets a local target change; the site's normal click handler runs.
        select = """(() => {
          const norm = s => String(s || '').normalize('NFKC').replace(/\\s+/g, '');
          const wanted = %s;
          const links = Array.from(document.querySelectorAll('.news-list h3 a'));
          const matched = links.filter(a => norm(a.textContent) === norm(wanted));
          if (matched.length !== 1) return {selected:false};
          const a = matched[0];
          const u = new URL(a.href, location.href);
          if (u.protocol !== 'https:' || u.hostname !== 'weixin.sogou.com' || u.pathname !== '/link' || u.username || u.password) return {selected:false};
          a.target = '_self'; a.setAttribute('data-social-read-selected', '1');
          return {selected:true};
        })()""" % json.dumps(title, ensure_ascii=True)
        selected = json.JSONDecoder().raw_decode(command(["eval", select, "--tab", tab]))[0]
        if not selected.get("selected"):
            raise ReadError("source_context_changed", "搜索页没有唯一匹配这篇文章。请重新搜索后读取，不能替换成另一篇文章。")
        command(["click", '.news-list h3 a[data-social-read-selected="1"]', "--tab", tab])
        try:
            wait_selector(runner, session, tab, "#js_content", cancel, 15)
        except ReadError:
            check_page()
            raise
        current = validate_url("weixin", command(["get", "url", "--tab", tab]), allow_index=False)
        html = command(["get", "html", "--selector", ".rich_media_area_primary", "--max", "1500000", "--tab", tab])
        if len(html) >= 1500000:
            raise ReadError("content_unavailable", "文章区域超过读取预算，未把截断内容当作完整正文。")
        parser = ArticleParser()
        parser.feed(html)
        article = parser.article(html)
        canonical_script = """(() => {
          const link = document.querySelector('link[rel="canonical"]');
          const meta = document.querySelector('meta[property="og:url"]');
          if (link && link.href) return {url:link.href};
          if (meta && meta.content) return {url:meta.content};
          for (const s of document.querySelectorAll('script:not([src])')) {
            const m = (s.textContent || '').match(/(?:var\\s+)?msg_link\\s*=\\s*["']([^"']+)["']/);
            if (m) return {url:m[1]};
          }
          return {url:location.href};
        })()"""
        canonical = json.JSONDecoder().raw_decode(command(["eval", canonical_script, "--tab", tab]))[0].get("url")
        try:
            current = validate_url("weixin", canonical, allow_index=False)
        except ReadError:
            pass
        article["url"] = current
        item = content_item("weixin", article, current, "opencli-sogou-click")
        item["media"] = article["media"]
        item["notice"] = "从搜索页正常打开原文，取得文章文字与图片链接；图片内文字未识别，完整性仍需核对原文。"
        return item
    except (ValueError, TypeError, AttributeError):
        raise ReadError("adapter_incompatible", "文章读取组件的返回格式发生变化。")
    finally:
        if tab and re.fullmatch(r"[\w-]{1,100}", tab):
            try:
                runner.run(["browser", session, "tab", "close", tab], timeout=5)
            except ReadError:
                pass
