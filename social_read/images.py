"""Bounded CDN downloads and optional local Vision / RapidOCR backends."""
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
from .model import ReadError, digest, scrub, public_url

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
    except HTTPError as exc:
        code = "image_rate_limited" if exc.code == 429 else "image_access_denied" if exc.code in (401, 403) else "image_unavailable"
        raise ReadError(code, "图片来源暂不可访问，已保留其他图片，未重复请求。")
    except (URLError, OSError, ValueError):
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
        self.backend = os.environ.get("SOCIAL_READ_OCR", "auto")
        if self.backend not in {"auto", "vision", "rapidocr"}:
            raise ReadError("invalid_input", "OCR 请选择 auto、vision 或 rapidocr。")
        self.python = Path(os.environ.get("SOCIAL_READ_RAPIDOCR_PYTHON", str(Path(__file__).resolve().parent.parent / ".state/rapidocr-env/bin/python")))
        # The worker and environment lock participate in cache identity.
        lock = Path(__file__).resolve().parent.parent / ".state/rapidocr-lock.json"
        if self.backend == "auto":
            self.backend = "rapidocr" if self.python.is_file() and lock.is_file() else "vision" if platform.system() == "Darwin" else "rapidocr"
        self.cache_version = CACHE_VERSION + "-" + digest((Path(__file__).parent/"native/vision_ocr.swift").read_text())[:16] if self.backend == "vision" else self.backend + "-v1-" + digest((Path(__file__).parent / "rapidocr_worker.py").read_text() + (lock.read_text() if lock.exists() else str(self.python)))[:16]

    def command(self, cancel, deadline=None):
        if self.backend == "vision" or (self.backend == "auto" and platform.system() == "Darwin" and shutil.which("swiftc")):
            return [str(self.binary(cancel, deadline))]
        if not self.python.is_file():
            raise ReadError("ocr_unavailable", "请让 Agent 运行 bootstrap.py --install-ocr 安装可选本机识别环境。")
        return [str(self.python), str(Path(__file__).parent / "rapidocr_worker.py")]

    def binary(self, cancel, deadline=None):
        if platform.system() != "Darwin" or not shutil.which("swiftc"):
            raise ReadError("ocr_unavailable", "图片识别目前需要 macOS Vision 与 Swift 编译工具。")
        source = Path(__file__).parent / "native/vision_ocr.swift"
        version = digest(source.read_text())[:16]
        binary = self.root / ("vision-" + version)
        if not binary.exists():
            with tempfile.TemporaryDirectory(dir=str(self.root)) as temp:
                candidate = Path(temp) / "vision"
                run_local([shutil.which("swiftc"), "-O", str(source), "-o", str(candidate)], cancel, min(60, remaining(deadline)))
                candidate.chmod(0o700)
                os.replace(candidate, binary)
        return binary

    def read(self, url, cancel, deadline=None):
        url = image_url(url)
        if cancel and cancel.is_set():
            raise ReadError("cancelled", "任务已取消。")
        index = self.root / ("url-" + digest(self.cache_version + "|" + url) + ".json")
        try:
            record = json.loads(index.read_text())
            sha = record.get("sha", "")
            age = time.time() - record["at"]
            if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha) and 0 <= age < 1800:
                cached = self.root / (self.cache_version + "-" + sha + ".json")
                result = json.loads(cached.read_text())
                if result.get("image_sha256") == sha:
                    result.update(cache_used=True, image_download_cache_used=True, cache_age_seconds=round(age))
                    return result
        except (OSError, ValueError, TypeError, KeyError):
            pass
        remaining(deadline)
        command = self.command(cancel, deadline)
        with tempfile.TemporaryDirectory(dir=str(self.root)) as temp:
            path = Path(temp) / "image"
            remaining(deadline)
            download_image(url, path, cancel, deadline)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            cached = self.root / (self.cache_version + "-" + sha + ".json")
            result = None
            if cached.exists():
                try:
                    result = json.loads(cached.read_text())
                    result["cache_used"] = True
                except (OSError, ValueError):
                    pass
            if result is None:
                seconds = min(45 if self.backend == "rapidocr" else 25, remaining(deadline))
                value = json.loads(run_local(command + [str(path)], cancel, seconds))
                used_rapid = "rapidocr_worker.py" in command[-1]
                lines = value.get("lines", [])
                if not isinstance(lines, list) or len(lines) > 5000:
                    raise ReadError("ocr_failed", "图片识别结果格式不符合要求。")
                text = scrub("\n".join(str(line.get("text", "")) for line in lines))
                result = {"status": value.get("status"), "text": text, "text_chars": len(text),
                          "image_sha256": sha, "backend": "rapidocr-onnx-local" if used_rapid else "macos-vision-local", "lines": lines,
                          "tiles": value.get("tiles", 1), "formula_semantics": False,
                          "fallback_status": value.get("fallback_status"),
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

def remaining(deadline):
    seconds = deadline - time.monotonic() if deadline else 60
    if seconds <= 0:
        raise ReadError("image_timeout", "本次图片读取时间预算已用完，已完成图片保留。")
    return seconds


def atomic_json(path, value):
    """Private, atomic checkpoints; a cancelled read cannot corrupt the last one."""
    fd, name = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def enrich_images(item, images, runner, cancel, order_source, reported_count=None, context=None, progress=None, image_ids=None):
    caption = item["body"]
    item["caption"] = caption
    item["media"] = []
    deadline = time.monotonic() + 150
    ocr = LocalOCR(runner.workdir)
    unique, identities, seen = [], [], set()
    for position, raw in enumerate(images[:40]):
        try:
            url = image_url(raw)
            if url not in seen:
                unique.append(url)
                ident = image_ids[position] if order_source == "note_state_carousel" and isinstance(image_ids, list) and position < len(image_ids) else None
                identities.append("state:" + ident if isinstance(ident, str) and 0 < len(ident) <= 500 else "url:" + url)
                seen.add(url)
        except ReadError:
            unique.append(None)
            identities.append("rejected:" + str(position))
    # Bind progress to the exact note and ordered image manifest. Never reuse an
    # image by its position alone; edited/reordered notes get a new checkpoint.
    note_identity = urlsplit(item["_target"]).path.rstrip("/").split("/")[-1]
    manifest = digest(ocr.cache_version + "|" + note_identity + "|" + json.dumps(identities))
    checkpoint = ocr.root / ("note-" + manifest + ".json")
    previous = {}
    try:
        saved = json.loads(checkpoint.read_text())
        if saved.get("manifest") == manifest and 0 <= time.time() - saved["at"] < 1800:
            previous = {r["order"]: r for r in saved["images"] if r.get("status") in {"success", "no_text_detected"}}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    expected = max(len(unique), reported_count or 0)
    rows = [{"order": i + 1, "source": "note_image", "order_source": order_source,
             "status": "pending", "text": "", "text_chars": 0} for i in range(min(expected, 40))]
    errors, reused, processed = [], 0, 0
    total_text = 0

    def publish():
        succeeded = sum(r["status"] in {"success", "no_text_detected"} for r in rows)
        parts = ["【笔记配文】\n" + caption]
        parts += ["【图片 %s · 本机 OCR，需核对识别错误】\n%s" % (r["order"], r["text"]) for r in rows if r.get("text")]
        item["body"] = scrub("\n\n".join(parts))
        item["body_chars"] = len(item["body"])
        item["body_sha256"] = digest(item["body"])
        item["image_ocr"] = [dict(r) for r in rows]
        item["image_ocr_chars"] = sum(r.get("text_chars", 0) for r in rows)
        item["coverage"] = {"caption": "read", "ocr_profile": ocr.cache_version, "image_count": expected, "discovered_images": len(unique),
            "ocr_attempted": processed, "ocr_succeeded": succeeded, "ocr_unread": expected - succeeded,
            "image_order": order_source, "images_all_processed": bool(expected) and succeeded == expected,
            "video_transcription": False, "ocr_accuracy_verified": False,
            "images_reused": reused, "images_processed_this_run": processed,
            "unread_orders": [r["order"] for r in rows if r["status"] not in {"success", "no_text_detected"}],
            "undiscovered_image_count": max(0, expected - len(unique)),
            "images_from_download_cache": sum(r.get("image_download_cache_used", False) for r in rows)}
        item["read_errors"] = list(errors)
        item["notice"] = ("已处理 %s/%s 张图片；成功结果逐张保存，未读图片可继续补读。OCR 可能错字，版面含义及视频未识别。" % (succeeded, expected)) if expected else "已读配文，未发现可识别图片。"
        atomic_json(checkpoint, {"manifest": manifest, "at": time.time(), "images": rows})
        if progress:
            progress(item)

    # Restore before publishing so even an immediate cancellation keeps successes.
    for i, url in enumerate(unique):
        if url:
            item["media"].append({"type": "image", "order": i + 1, "url": url})
        if i + 1 in previous:
            rows[i].update(previous[i + 1], checkpoint_reused=True)
            reused += 1
            total_text += rows[i].get("text_chars", 0)
    publish()
    blocked = None
    for index, row in enumerate(rows):
        if cancel and cancel.is_set():
            raise ReadError("cancelled", "任务已取消，逐张完成的图片已保存，可再次读取补齐。")
        if row.get("checkpoint_reused"):
            continue
        url = unique[index] if index < len(unique) else None
        if blocked:
            row.update(status="not_read_access_stop", message="图片来源要求停止，未继续请求。")
        elif index >= MAX_IMAGES or time.monotonic() >= deadline or total_text >= 80000:
            row.update(status="not_read_budget")
        elif not url:
            row.update(status="image_url_rejected" if index < len(unique) else "image_not_discovered")
        else:
            try:
                processed += 1
                recognized = ocr.read(url, cancel, deadline)
                row.update({k: v for k, v in recognized.items() if k != "lines"})
                total_text += row.get("text_chars", 0)
            except ReadError as exc:
                if exc.code == "cancelled":
                    publish()
                    raise
                row.update(status=exc.code, message=exc.message)
                errors.append({"image_order": index + 1, **exc.result()})
                if exc.code in {"image_access_denied", "image_rate_limited"}:
                    blocked = exc.code
        publish()
    return item
