import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import TERMINAL
from .model import ReadError, validate_url

STATIC = Path(__file__).parent / "static"


def handler_for(engine, port, token=None):
    session_token = token or secrets.token_urlsafe(32)
    allowed_hosts = {"127.0.0.1:%s" % port, "localhost:%s" % port}
    allowed_origins = {"http://" + h for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log request URLs, tokens, query strings or signed links.

        def guard(self, authenticated=False):
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in allowed_hosts or (origin and origin not in allowed_origins) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.send_json({"status": "forbidden", "message": "仅允许本机页面访问。"}, 403)
                return False
            if authenticated and not hmac.compare_digest(self.headers.get("X-Social-Read-Token", ""), session_token):
                self.send_json({"status": "forbidden", "message": "请刷新本机页面后重试。"}, 403)
                return False
            return True

        def headers_common(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

        def send_json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.headers_common()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if not self.guard(path.startswith("/api/") and path != "/api/bootstrap"):
                return
            try:
                if path == "/api/bootstrap":
                    return self.send_json({"token": session_token, **engine.capabilities()})
                if path == "/api/history":
                    return self.send_json({"jobs": engine.store.history()})
                if path.startswith("/api/tasks/"):
                    return self.send_json(engine.store.job(path.rsplit("/", 1)[-1]))
                if path not in {"/", "/index.html", "/app.js", "/style.css"}:
                    return self.send_json({"status": "not_found"}, 404)
                f = STATIC / ("index.html" if path == "/" else path[1:])
                body = f.read_bytes()
                self.send_response(200)
                self.headers_common()
                self.send_header("Content-Type", {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[f.suffix])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except ReadError as exc:
                self.send_json(exc.result(), 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send_json({"status": "internal_error", "message": "请求未完成，请重新检查连接。"}, 500)

        def do_POST(self):
            if not self.guard(True):
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ReadError("invalid_input", "请求须为 JSON。")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 64 * 1024:
                    raise ReadError("invalid_input", "请求大小无效。")
                data = json.loads(self.rfile.read(size))
                path = urlsplit(self.path).path
                if path == "/api/source":
                    if engine.mode != "live" or not isinstance(data, dict) or set(data) != {"content_ref"} or not isinstance(data["content_ref"], str):
                        raise ReadError("invalid_input", "请从真实内容记录打开来源。")
                    item = engine.store.item(data["content_ref"])
                    # Only the authenticated local UI receives the private access URL.
                    # MCP and task/history responses remain redacted.
                    return self.send_json({"url": validate_url(item["platform"], item["target"])})
                if path == "/api/tasks":
                    if not isinstance(data, dict) or set(data) != {"action", "request"}:
                        raise ReadError("invalid_input", "任务参数无效。")
                    return self.send_json(engine.submit(data["action"], data["request"]), 202)
                if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                    return self.send_json(engine.cancel(path.split("/")[-2]))
                return self.send_json({"status": "not_found"}, 404)
            except (ReadError, ValueError) as exc:
                self.send_json(exc.result() if isinstance(exc, ReadError) else {"status": "invalid_input", "message": "请求不是有效 JSON。"}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send_json({"status": "internal_error", "message": "请求未完成，请重新提交。"}, 500)

    return Handler


def serve(engine, port=8767):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(engine, port))
    print("social-media-read-for-agent: http://127.0.0.1:%s · %s" % (port, "离线演示" if engine.mode == "demo" else "真实读取"), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
