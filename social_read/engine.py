import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .adapter import DemoAdapter, OpenCLIAdapter, Runner
from .model import PLATFORMS, ReadError, bounded_int, digest, keyword, now, platform, scrub, validate_url
from .storage import Store

TERMINAL = {"success", "partial", "failed", "cancelled", "interrupted"}


class Engine:
    def __init__(self, state_dir, mode="live", profile=None, runner=None, adapter=None):
        if mode not in ("live", "demo"):
            raise ReadError("invalid_input", "模式必须是 live 或 demo。")
        self.mode = mode
        self.profile = profile
        # Keep demo and named browser profiles in different state scopes.
        scope = mode + "-" + digest(profile or "default")[:12]
        self.store = Store(Path(state_dir) / scope)
        self.runner = runner or Runner(self.store.root, profile=profile)
        self.adapter = adapter or (DemoAdapter() if mode == "demo" else OpenCLIAdapter(self.runner))
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.events = {}
        self.events_lock = threading.RLock()
        self.locks = {s: threading.Lock() for s in PLATFORMS}
        self.closed = False

    def capabilities(self):
        return {"mode": self.mode, "read_only": True, "platforms": [{"id": s, "label": d["label"], "search_source": d["source"], "search": "candidate", "read": "candidate", "comments": "candidate" if d["comments"] else "unsupported", "live_validation": "pending", "limitations": {"xiaohongshu": "当前组件未返回图片正文，图片内文字需到原帖查看。", "weibo": "长文完整性和转发关系需实测；评论只取有限范围。", "zhihu": "问题页最多读取三个回答；文章评论暂未支持。", "weixin": "搜索来自搜狗索引，跳转链接需提供对应微信文章地址。"}[s]} for s, d in PLATFORMS.items()]}

    def _validate(self, action, request):
        if action not in {"search", "read", "comments", "connection"} or not isinstance(request, dict):
            raise ReadError("invalid_input", "不支持的读取任务。")
        allowed = {"search": {"platforms", "query", "limit"}, "read": {"platform", "url", "content_ref", "refresh"}, "comments": {"platform", "url", "content_ref", "limit"}, "connection": set()}[action]
        if set(request) - allowed:
            raise ReadError("invalid_input", "任务包含不支持的参数。")
        if self.mode == "live" and action != "connection" and not self.profile:
            raise ReadError("profile_required", "请先指定专用浏览器资料。运行启动器设置资料后再读取。")
        r = dict(request)
        if action == "search":
            r["query"] = keyword(r.get("query"))
            sites = r.get("platforms")
            if not isinstance(sites, list) or not 1 <= len(sites) <= 4 or any(not isinstance(s, str) for s in sites) or len(set(sites)) != len(sites):
                raise ReadError("invalid_input", "请选 1 至 4 个不同平台。")
            r["platforms"] = [platform(s) for s in sites]
            r["limit"] = bounded_int(r.get("limit", 5))
        elif action in {"read", "comments"}:
            if bool(r.get("content_ref")) == bool(r.get("url")):
                raise ReadError("invalid_input", "请提供内容记录或完整链接，二选一。")
            if r.get("content_ref"):
                if not isinstance(r["content_ref"], str):
                    raise ReadError("invalid_input", "内容记录标识须为字符串。")
                item = self.store.item(r["content_ref"])
                r["platform"] = item["platform"]
                r["_target"] = item["target"]
            else:
                r["platform"] = platform(r.get("platform"))
                r["_target"] = validate_url(r["platform"], r["url"])
            if action == "comments":
                r["limit"] = bounded_int(r.get("limit", 10), maximum=20)
            else:
                if "refresh" in r and not isinstance(r["refresh"], bool):
                    raise ReadError("invalid_input", "刷新参数须为布尔值。")
                r["refresh"] = r.get("refresh", False)
        return r

    def submit(self, action, request):
        if self.closed:
            raise ReadError("unavailable", "服务正在停止。")
        r = self._validate(action, request)
        with self.events_lock:
            active = sum(not e.is_set() and self.store.job(i)["status"] not in TERMINAL for i, e in self.events.items())
            if active >= 8:
                raise ReadError("busy", "当前任务较多，请等待或取消任务后再试。")
            public_request = {k: v for k, v in r.items() if not k.startswith("_")}
            job_id = self.store.create_job(action, public_request)
            event = threading.Event()
            self.events[job_id] = event
        self.pool.submit(self._work, job_id, action, r, event)
        return {"task_id": job_id, "status": "queued", "mode": self.mode, "next": "使用 get_task 查询任务，完成后用 content_ref 阅读。"}

    def _execute(self, site, event, fn):
        lock = self.locks[site]
        while not lock.acquire(timeout=0.1):
            if event.is_set():
                raise ReadError("cancelled", "任务已取消。")
        handle = None
        try:
            # Cross-process read serialization for CLI/MCP/dashboard sharing state.
            try:
                import fcntl
                handle = open(self.store.root / (site + ".lock"), "a")
                os.chmod(handle.name, 0o600)
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if event.wait(0.1):
                            raise ReadError("cancelled", "任务已取消。")
            except ImportError:
                pass
            if event.is_set():
                raise ReadError("cancelled", "任务已取消。")
            result = fn()
            if event.is_set():
                raise ReadError("cancelled", "任务已取消，完成记录已保留。")
            return result
        finally:
            if handle:
                handle.close()
            lock.release()

    def _work(self, job_id, action, r, event):
        result = {"mode": self.mode, "items": [], "errors": [], "fetched_at": now()}
        status = "success"
        try:
            if event.is_set():
                raise ReadError("cancelled", "任务已取消。")
            self.store.update_job(job_id, "running", result)
            if action == "search":
                for site in r["platforms"]:
                    try:
                        items = self._execute(site, event, lambda: self.adapter.search(site, r["query"], r["limit"], event))
                        seen = set()
                        for item in items:
                            saved = self.store.save_item(site, item, raw=item)
                            if saved["content_ref"] not in seen:
                                result["items"].append(saved)
                                seen.add(saved["content_ref"])
                        if not items:
                            result["errors"].append({"platform": site, "status": "empty", "message": "组件本次未返回结果，不能据此判断平台没有相关内容。"})
                    except ReadError as e:
                        if e.code == "cancelled":
                            raise
                        result["errors"].append({"platform": site, **e.result()})
                    self.store.update_job(job_id, "running", result)
                failures = [e for e in result["errors"] if e["status"] != "empty"]
                status = "partial" if failures and result["items"] else "failed" if failures else "success"
                result["coverage"] = {"requested_platforms": r["platforms"], "max_results_per_platform": r["limit"], "all_content": False}
            elif action == "read":
                cached = self.store.item(r["content_ref"]) if r.get("content_ref") else None
                age = None
                if cached and cached["read_at"]:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["read_at"])).total_seconds()
                if age is not None and age < 1800 and not r["refresh"]:
                    result["content"] = {**cached["data"], "cache": {"used": True, "age_seconds": round(age)}}
                else:
                    item = self._execute(r["platform"], event, lambda: self.adapter.read(r["platform"], r["_target"], event))
                    result["content"] = self.store.save_item(r["platform"], item, raw=item.get("_raw"), read=True)
                    result["content"]["cache"] = {"used": False}
                answer_errors = result.get("content", {}).get("answer_errors", [])
                if answer_errors:
                    result["errors"].extend({"platform": r["platform"], **e} for e in answer_errors)
                    status = "partial"
            elif action == "comments":
                result.update(self._execute(r["platform"], event, lambda: self.adapter.comments(r["platform"], r["_target"], r["limit"], event)))
                result["source_url"] = scrub(r["_target"])
                result["platform"] = r["platform"]
            elif action == "connection":
                if self.mode == "demo":
                    result["connection"] = {"status": "demo", "message": "离线演示，未检查浏览器或真实账号。"}
                else:
                    result["connection"] = self.runner.diagnose(event)
                    if result["connection"]["status"] == "connected":
                        result["platforms"] = []
                        for site in ("xiaohongshu", "weibo", "zhihu"):
                            try:
                                data = self._execute(site, event, lambda: self.runner.call(site, "whoami", [], event))
                                result["platforms"].append({"platform": site, "status": "identity_reported", "message": "账号检查返回了身份信息；实际搜索与阅读仍需验证。", "identity": scrub(data), "search_validation": "pending"})
                            except ReadError as e:
                                result["platforms"].append({"platform": site, **e.result()})
                        result["platforms"].append({"platform": "weixin", "status": "public_index", "message": "公众号搜索不使用公众号管理权限；需实际检索验证。"})
        except ReadError as e:
            if action == "read" and e.code == "empty":
                e = ReadError("content_unavailable", "没有取得可见正文。可能是内容受限、页面变化或组件不兼容；请到原帖核对。")
            elif action == "comments" and e.code == "empty":
                e = ReadError("comments_empty", "此次未取得评论。可能没有可见评论或组件未识别；请到原帖核对。")
            result["errors"].append(e.result())
            status = "cancelled" if e.code == "cancelled" else "failed"
        except Exception:
            # Unknown failures must not leak raw exceptions or private access links.
            result["errors"].append({"status": "internal_error", "message": "服务未完成此任务，请查看验证记录或重新提交。"})
            status = "failed"
        finally:
            self.store.update_job(job_id, "cancelled" if event.is_set() else status, result)
            with self.events_lock:
                self.events.pop(job_id, None)

    def cancel(self, task_id):
        with self.events_lock:
            job = self.store.job(task_id)
            if job["status"] in TERMINAL:
                return job
            event = self.events.get(task_id)
            if event:
                event.set()
                return {"task_id": task_id, "status": "cancelling", "message": "正在停止读取，已完成的内容保留。"}
            raise ReadError("different_process", "此任务由另一运行实例执行，请在该实例中取消。")

    def wait(self, task_id, timeout=120):
        deadline = time.monotonic() + timeout
        while True:
            job = self.store.job(task_id)
            if job["status"] in TERMINAL or time.monotonic() >= deadline:
                return job
            time.sleep(0.05)

    def shutdown(self):
        self.closed = True
        with self.events_lock:
            for event in self.events.values():
                event.set()
        self.pool.shutdown(wait=True)
        self.store.close()
