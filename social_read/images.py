"""Bounded CDN image downloads and local macOS Vision OCR."""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .model import ReadError, digest, scrub

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGES = 18
CACHE_VERSION = "vision-zh-v1"

def image_url(value):
    if not isinstance(value, str) or len(value) > 5000:
        raise ReadError("image_url_rejected", "图片地址不符合读取范围。")
    try:
        p = urlsplit(value if not value.startswith("//") else "https:" + value)
        host = p.hostname or ""
        if p.scheme not in ("https", "http") or not (host == "xhscdn.com" or host.endswith(".xhscdn.com")) or p.username or p.password or p.port not in (None, 80, 443):
            raise ValueError()
        return urlunsplit(p._replace(scheme="https", netloc=host, fragment=""))
    except ValueError:
        raise ReadError("image_url_rejected", "图片地址不符合小红书图片来源范围。")

class ImageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, image_url(newurl))

def download_image(url, path, cancel, deadline=None):
    url = image_url(url)
    stop = min(time.monotonic() + 12, deadline) if deadline else time.monotonic() + 12
    try:
        with build_opener(ImageRedirect()).open(Request(url, headers={"Accept": "image/*", "Referer": "https://www.xiaohongshu.com/"}), timeout=max(0.5, min(12, stop-time.monotonic()))) as response:
            image_url(response.geturl())
            kind = response.headers.get_content_type()
            if kind not in {"image/jpeg", "image/png", "image/webp", "image/avif", "image/heic", "application/octet-stream"}:
                raise ReadError("image_decode_failed", "图片来源没有返回支持的图片。")
            if int(response.headers.get("Content-Length", "0")) > MAX_IMAGE_BYTES:
                raise ReadError("image_budget_exceeded", "图片超过单张读取预算。")
            size = 0
            with open(path, "wb") as dest:
                while True:
                    if cancel and cancel.is_set():
                        raise ReadError("cancelled", "任务已取消。")
                    if time.monotonic() > stop:
                        raise ReadError("image_timeout", "图片下载超过时间预算。")
                    chunk = (response.read1 if hasattr(response, "read1") else response.read)(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ReadError("image_budget_exceeded", "图片超过单张读取预算。")
                    dest.write(chunk)
            if not size:
                raise ReadError("image_decode_failed", "图片为空。")
    except (URLError, HTTPError, OSError, ValueError):
        raise ReadError("image_unavailable", "本次未取得图片，配文及其他已读图片保留。")

def run_local(args, cancel, timeout):
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(args, stdout=out, stderr=err)
        try:
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if cancel and cancel.is_set():
                    raise ReadError("cancelled", "任务已取消。")
                if time.monotonic() > deadline:
                    raise ReadError("ocr_timeout", "本机图片识别超过时间预算。")
                if out.tell() > 2 * 1024 * 1024 or err.tell() > 1024 * 1024:
                    raise ReadError("ocr_failed", "本机图片识别输出超出预算。")
                time.sleep(0.05)
            out.seek(0)
            value = out.read(2 * 1024 * 1024).decode("utf-8", "replace")
            if proc.returncode:
                # Preserve only known structured status, never compiler/system logs.
                try:
                    status = json.loads(value).get("status")
                except ValueError:
                    status = None
                raise ReadError(status if status in {"image_decode_failed", "image_budget_exceeded"} else "ocr_unavailable", "本机图片识别未完成。")
            return value
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=2)

class LocalOCR:
    def __init__(self, root):
        self.root = Path(root) / "image-ocr"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.root.chmod(0o700)

    def binary(self, cancel):
        if platform.system() != "Darwin" or not shutil.which("swiftc"):
            raise ReadError("ocr_unavailable", "图片识别目前需要 macOS Vision 与 Swift 编译工具。")
        source = Path(__file__).parent / "native/vision_ocr.swift"
        version = digest(source.read_text())[:16]
        binary = self.root / ("vision-" + version)
        if not binary.exists():
            with tempfile.TemporaryDirectory(dir=str(self.root)) as temp:
                candidate = Path(temp) / "vision"
                run_local([shutil.which("swiftc"), "-O", str(source), "-o", str(candidate)], cancel, 60)
                candidate.chmod(0o700)
                os.replace(candidate, binary)
        return binary

    def read(self, url, cancel, deadline=None):
        url = image_url(url)
        if cancel and cancel.is_set():
            raise ReadError("cancelled", "任务已取消。")
        index = self.root / ("url-" + digest(CACHE_VERSION + "|" + url) + ".json")
        try:
            record = json.loads(index.read_text())
            sha = record.get("sha", "")
            age = time.time() - record["at"]
            if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha) and 0 <= age < 1800:
                cached = self.root / (CACHE_VERSION + "-" + sha + ".json")
                result = json.loads(cached.read_text())
                if result.get("image_sha256") == sha:
                    result.update(cache_used=True, image_download_cache_used=True, cache_age_seconds=round(age))
                    return result
        except (OSError, ValueError, TypeError, KeyError):
            pass
        binary = self.binary(cancel)
        with tempfile.TemporaryDirectory(dir=str(self.root)) as temp:
            path = Path(temp) / "image"
            download_image(url, path, cancel)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            cached = self.root / (CACHE_VERSION + "-" + sha + ".json")
            result = None
            if cached.exists():
                try:
                    result = json.loads(cached.read_text())
                    result["cache_used"] = True
                except (OSError, ValueError):
                    pass
            if result is None:
                seconds = min(25, max(1, deadline - time.monotonic())) if deadline else 25
                value = json.loads(run_local([str(binary), str(path)], cancel, seconds))
                lines = value.get("lines", [])
                if not isinstance(lines, list) or len(lines) > 5000:
                    raise ReadError("ocr_failed", "图片识别结果格式不符合要求。")
                text = scrub("\n".join(str(line.get("text", "")) for line in lines))
                result = {"status": value.get("status"), "text": text, "text_chars": len(text),
                          "image_sha256": sha, "backend": "macos-vision-local", "lines": lines,
                          "width": value.get("width"), "height": value.get("height"),
                          "resized": value.get("resized", False), "cache_used": False}
                if len(text) > 50000:
                    raise ReadError("image_budget_exceeded", "图片识别文字超过单张预算。")
                with open(cached, "w") as f:
                    json.dump(result, f, ensure_ascii=False)
                cached.chmod(0o600)
            result["image_download_cache_used"] = False
            if result.get("status") in {"success", "no_text_detected"}:
                with open(index, "w") as f:
                    json.dump({"sha": sha, "at": time.time()}, f)
                index.chmod(0o600)
            return result

def enrich_images(item, images, runner, cancel, order_source, reported_count=None):
    caption = item["body"]
    item["caption"] = caption
    item["image_ocr"] = []
    item["media"] = []
    errors = []
    started = time.monotonic()
    deadline = started + 150
    ocr = LocalOCR(runner.workdir)
    unique = []
    seen = set()
    for raw in images:
        try:
            url = image_url(raw)
            if url not in seen:
                unique.append(url); seen.add(url)
        except ReadError as exc:
            # Preserve rejected positions as an explicit unread slot.
            unique.append(None)
    total_text = 0
    for index, url in enumerate(unique):
        row = {"order": index + 1, "source": "note_image", "order_source": order_source}
        if url:
            item["media"].append({"type": "image", "order": index + 1, "url": url})
        if index >= MAX_IMAGES or time.monotonic() > deadline or total_text >= 80000:
            row.update(status="not_read_budget", text="", text_chars=0)
        elif not url:
            row.update(status="image_url_rejected", text="", text_chars=0)
        else:
            try:
                recognized = ocr.read(url, cancel, deadline)
                row.update({k:v for k,v in recognized.items() if k != "lines"})
                total_text += row.get("text_chars", 0)
            except ReadError as exc:
                if exc.code == "cancelled":
                    raise
                row.update(status=exc.code, text="", text_chars=0, message=exc.message)
                errors.append({"image_order": index + 1, **exc.result()})
        item["image_ocr"].append(row)
    attempted = sum(r["status"] not in {"not_read_budget", "image_url_rejected"} for r in item["image_ocr"])
    succeeded = sum(r["status"] in {"success", "no_text_detected"} for r in item["image_ocr"])
    text_parts = ["【笔记配文】\n" + caption]
    for row in item["image_ocr"]:
        if row.get("text"):
            text_parts.append("【图片 %s · 本机 OCR，需核对识别错误】\n%s" % (row["order"], row["text"]))
    item["body"] = scrub("\n\n".join(text_parts))
    item["body_chars"] = len(item["body"])
    item["body_sha256"] = digest(item["body"])
    item["image_ocr_chars"] = sum(r.get("text_chars", 0) for r in item["image_ocr"])
    expected = max(len(unique), reported_count or 0)
    item["coverage"] = {"caption": "read", "image_count": expected, "discovered_images": len(unique),
                        "ocr_attempted": attempted, "ocr_succeeded": succeeded,
                        "ocr_unread": expected - succeeded, "image_order": order_source,
                        "images_all_processed": bool(expected) and succeeded == expected,
                        "video_transcription": False, "ocr_accuracy_verified": False, "images_from_download_cache": sum(r.get("image_download_cache_used", False) for r in item["image_ocr"])}
    item["read_errors"] = errors
    if expected and succeeded < expected:
        item["notice"] = "已读配文及部分图片；还有 %s 张图片未识别。OCR 可能错字，不能视为完整读懂图文。" % (expected - succeeded)
    elif expected:
        item["notice"] = "已取得配文与 %s 张图片的本机 OCR；图片逐张分开并保留顺序。OCR 可能错字，版面关系及视频未识别。" % expected
    else:
        item["notice"] = "已取得配文，本次没有发现可读取的图片；不能据此判断原帖没有图片。"
    return item
