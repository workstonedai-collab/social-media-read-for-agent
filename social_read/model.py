import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, quote

PLATFORMS = {
    "xiaohongshu": {"label": "小红书", "source": "小红书网页", "comments": True},
    "weibo": {"label": "微博", "source": "微博网页", "comments": True},
    "zhihu": {"label": "知乎", "source": "知乎网页", "comments": True},
    "weixin": {"label": "微信公众号", "source": "搜狗微信索引", "comments": False},
}
HOSTS = {
    "xiaohongshu": {"www.xiaohongshu.com", "xiaohongshu.com"},
    "weibo": {"weibo.com", "www.weibo.com", "m.weibo.cn"},
    "zhihu": {"www.zhihu.com", "zhihu.com", "zhuanlan.zhihu.com"},
    "weixin": {"mp.weixin.qq.com", "weixin.sogou.com"},
}
SECRET_KEYS = {"cookie", "authorization", "access_token", "refresh_token", "token", "xsec_token", "api_key", "password", "secret", "signature"}


class ReadError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable

    def result(self):
        return {"status": self.code, "message": self.message, "retryable": self.retryable}


def now():
    return datetime.now(timezone.utc).isoformat()


def platform(value):
    if not isinstance(value, str) or value not in PLATFORMS:
        raise ReadError("invalid_input", "请选择小红书、微博、知乎或微信公众号。")
    return value


def bounded_int(value, minimum=1, maximum=10):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ReadError("invalid_input", "数量须为 %s 到 %s 的整数。" % (minimum, maximum))
    return value


def keyword(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ReadError("invalid_input", "请输入 1 至 200 字的搜索词。")
    value = value.strip()
    if value.startswith("-") or any(ord(c) < 32 for c in value):
        raise ReadError("invalid_input", "搜索词不能以命令选项开头或包含控制字符。")
    return value


def validate_url(site, value, allow_index=True):
    platform(site)
    if not isinstance(value, str) or len(value) > 6000 or any(ord(c) < 32 for c in value):
        raise ReadError("invalid_input", "请输入完整的平台内容链接。")
    try:
        p = urlsplit(html.unescape(value.strip()))
        if p.scheme not in ("https", "http") or p.hostname not in HOSTS[site] or p.username or p.password or p.port not in (None, 80, 443):
            raise ValueError()
        if p.fragment:
            p = p._replace(fragment="")
        path = p.path
        allowed = {
            "xiaohongshu": bool(re.fullmatch(r"/(?:explore|discovery/item|search_result)/[a-fA-F0-9]{24}/?", path)),
            "weibo": bool(re.fullmatch(r"/(?:\d+/[A-Za-z0-9]+|detail/\d+|status/[A-Za-z0-9]+)/?", path)),
            "zhihu": bool(re.fullmatch(r"/(?:question/\d+(?:/answer/\d+)?|p/\d+)/?", path)),
            "weixin": (p.hostname == "mp.weixin.qq.com" and (path == "/s" or path.startswith("/s/"))) or (allow_index and p.hostname == "weixin.sogou.com" and path == "/link"),
        }[site]
        if not allowed:
            raise ValueError()
        # Always use encrypted transport, and never accept credential-bearing URLs.
        return urlunsplit(p._replace(scheme="https", netloc=p.hostname, path=quote(p.path, safe="/:@-._~!$&'()*+,;=%"), query=quote(p.query, safe="=&?/:@-._~!$'()*+,;%")))
    except (ValueError, AttributeError):
        raise ReadError("invalid_input", "此链接不是支持的平台内容地址；请复制原帖或文章链接。")


def public_url(value):
    if not isinstance(value, str):
        return ""
    try:
        p = urlsplit(value)
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in SECRET_KEYS]
        return urlunsplit(p._replace(query=urlencode(query), fragment=""))
    except ValueError:
        return ""


def scrub(value):
    """Sanitize output, recursively. Private signed access links stay only in state."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k.lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"https?://[^\s<>\"']+", lambda m: public_url(m.group()), value)
        value = re.sub(r"(?i)\b(cookie|authorization|access_token|refresh_token|api_key|password|xsec_token|signature)\s*[:=]\s*[^\s,;]+", r"\1=[已隐藏]", value)
    return value


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def strip_markup(value):
    return html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))


def unpack(data):
    if isinstance(data, dict) and data.get("ok") is False:
        raise ReadError("adapter_error", "读取组件返回失败。")
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, list) and data and all(isinstance(x, dict) and "field" in x and "value" in x for x in data):
        return {x["field"]: x["value"] for x in data}
    return data


def rows(data):
    data = unpack(data)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ReadError("adapter_incompatible", "读取组件返回了无法识别的数据格式。")


def search_item(site, row, query, backend):
    if not isinstance(row, dict):
        raise ReadError("adapter_incompatible", "搜索结果格式发生变化。")
    target = row.get("url") or ""
    title = row.get("title") or row.get("text") or row.get("question") or ""
    if not title or not target:
        raise ReadError("adapter_incompatible", "搜索结果缺少标题或来源链接。")
    validate_url(site, target)
    return {
        "platform": site, "title": (strip_markup(title)[:100] + ("…" if len(strip_markup(title)) > 100 else "")) if site == "weibo" else strip_markup(title), "title_is_excerpt": site == "weibo", "author": row.get("author") or None,
        "published_at_raw": None if site == "xiaohongshu" and backend == "opencli" else row.get("published_at") or row.get("publish_time") or row.get("time") or None,
        "date_estimate": row.get("published_at") if site == "xiaohongshu" and backend == "opencli" else None,
        "date_estimate_method": "note_id_timestamp" if site == "xiaohongshu" and backend == "opencli" else None,
        "source_url": public_url(target), "source": PLATFORMS[site]["source"],
        "type": row.get("type") or "content", "summary": strip_markup(row.get("summary") or ""),
        "rank": row.get("rank"), "query": query, "fetched_at": now(), "backend": backend,
        "notice": "搜索结果；阅读后才能取得正文。", "_target": target,
    }


def content_item(site, data, target, backend, markdown=None):
    d = unpack(data)
    if isinstance(d, list):
        d = next((r for r in d if isinstance(r, dict)), {})
    if not isinstance(d, dict):
        raise ReadError("adapter_incompatible", "正文数据格式无法识别。")
    body = markdown if markdown is not None else d.get("content") or d.get("text") or ""
    body = strip_markup(body) if markdown is None else body
    if not str(body).strip():
        raise ReadError("content_unavailable", "没有取得可见正文。可能是图文、权限限制或页面变化；请到原帖核对。")
    return {
        "platform": site, "title": d.get("title") or d.get("question_title") or "内容详情",
        "author": d.get("author") or None, "body": body,
        "published_at_raw": d.get("created_at") or d.get("publish_time") or None,
        "source_url": public_url(d.get("url") or target), "source": PLATFORMS[site]["source"] if site != "weixin" else "微信文章页面",
        "fetched_at": now(), "backend": backend, "completeness": "unverified",
        "body_sha256": digest(body), "media": [], "retweet": {"author": d.get("retweeted_from"), "body": d.get("retweeted_text")} if d.get("retweeted_from") else None,
        "notice": "已取得文字正文；完整性需对照原帖。图片内文字、视频语音不包含在本次结果中。",
        "_target": target, "_raw": data,
    }


def classify_error(message, returncode=None):
    """Inspect raw errors, return fixed safe messages instead of upstream logs."""
    m = str(message).lower()
    cases = [
        (r"operation not permitted|eperm|eacces", "environment_restricted", "当前执行环境限制本地服务或连接。请从本机启动器运行后再检查。"),
        (r"captcha|安全验证|验证码|verification|security_block|security block", "verification_required", "平台要求验证。请在平台页面完成验证后重试。"),
        (r"rate.limit|429|访问过于频繁|too many|频繁", "rate_limited", "平台限制了本次请求。任务已停止，请稍后检查。"),
        (r"auth.required|not logged|unauthorized|请登录|需要登录|session.expir", "login_required", "请在选定浏览器中登录此平台，然后重新检查连接。"),
        (r"navigation rejected|pre-navigation.*failed", "navigation_rejected", "浏览器没有完成平台页面跳转。请确认目标页面能正常打开后重试；这不代表账号被封。"),
        (r"bridge|daemon|extension|browser.*connect|连接浏览器", "bridge_unavailable", "浏览器连接未就绪。请检查 OpenCLI 扩展、本地服务及浏览器。"),
        (r"timeout|timed out|超时|deadline", "timeout", "本次读取超时，已保留完成的结果。"),
        (r"empty.result|no results|未找到|没有找到", "empty", "本次搜索没有结果。"),
        (r"no title|no content|content.*empty", "content_unavailable", "没有取得文章标题或正文。可能是访问受限、页面变化或组件不兼容；请到原文核对。"),
        (r"not found|deleted|不可用|不存在", "content_unavailable", "内容已删除、受限或不可访问。"),
    ]
    for pattern, code, text in cases:
        if re.search(pattern, m):
            return ReadError(code, text, code == "timeout")
    if returncode == 77:
        return ReadError("login_required", "请在浏览器中登录此平台。")
    if returncode == 69:
        return ReadError("bridge_unavailable", "浏览器连接未就绪，请检查扩展与本地服务。")
    return ReadError("adapter_error", "读取组件未完成操作。请检查连接状态或查看兼容记录。")
