import json
import mimetypes
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import API, APIError
from .config import VERSION

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://lain.bgm.tv; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def _query_dict(query_string):
    return urllib.parse.parse_qs(query_string or "", keep_blank_values=True)


def _route(api, method, path, query, body):
    segments = [part for part in path.strip("/").split("/") if part]
    if not segments:
        raise APIError("NOT_FOUND", "接口不存在", 404)
    if segments[0] != "api":
        raise APIError("NOT_FOUND", "接口不存在", 404)
    tail = segments[1:]
    if not tail:
        raise APIError("NOT_FOUND", "接口不存在", 404)
    head = tail[0]
    if method == "GET":
        if head == "bootstrap":
            return api.bootstrap()
        if head == "health":
            return api.health()
        if head == "settings":
            return api.get_settings()
        if head == "facets":
            return api.facets()
        if head == "search":
            return api.search(query)
        if head == "works" and len(tail) == 1:
            return api.works(query)
        if head == "works" and len(tail) == 2:
            return api.work_detail(tail[1])
        if head == "works" and len(tail) == 3 and tail[2] == "reviews":
            return api.reviews(tail[1], query)
        if head == "reviews" and len(tail) == 2:
            return api.review_detail(tail[1])
        if head == "jobs" and len(tail) == 1:
            return api.jobs()
        if head == "jobs" and len(tail) == 2:
            return api.job(tail[1])
        if head == "chat" and len(tail) == 2 and tail[1] == "sessions":
            return api.chat_sessions()
        if head == "chat" and len(tail) == 3 and tail[1] == "sessions":
            return api.get_session(tail[2])
        if head == "export":
            return api.export_data()
    if method == "POST":
        if head == "settings":
            return api.update_settings(body)
        if head == "ai" and len(tail) == 2 and tail[1] == "test":
            return api.ai_test()
        if head == "import":
            return api.import_work(body)
        if head == "works" and len(tail) == 2 and tail[1] == "merge":
            return api.merge(body)
        if head == "works" and len(tail) == 3 and tail[2] == "crawl":
            return api.recrawl(tail[1], body)
        if head == "jobs" and len(tail) == 3 and tail[2] == "cancel":
            return api.cancel_job(tail[1])
        if head == "jobs" and len(tail) == 3 and tail[2] == "retry":
            return api.retry_job(tail[1])
        if head == "bangumi" and len(tail) == 2 and tail[1] == "check":
            return api.bangumi_check()
        if head == "chat" and len(tail) == 2 and tail[1] == "estimate":
            return api.chat_estimate(body)
        if head == "chat" and len(tail) == 2 and tail[1] == "sessions":
            return api.create_session(body)
    if method == "PUT":
        if head == "settings":
            return api.update_settings(body)
    if method == "DELETE":
        if head == "works" and len(tail) == 2:
            return api.delete_work(tail[1])
        if head == "chat" and len(tail) == 3 and tail[1] == "sessions":
            return api.delete_session(tail[2])
        if head == "jobs" and len(tail) == 2:
            return api.delete_job(tail[1])
    raise APIError("NOT_FOUND", "接口不存在", 404)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ACGRadar/" + VERSION

    def log_message(self, fmt, *args):
        if getattr(self.server, "logger", None):
            self.server.logger.debug("%s - %s", self.address_string(), fmt % args)

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed = {
            "http://127.0.0.1:" + str(self.server.server_address[1]),
            "http://localhost:" + str(self.server.server_address[1]),
        }
        return origin in allowed

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _csrf_ok(self):
        return self.headers.get("X-CSRF-Token") == self.server.app.csrf

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2 * 1024 * 1024:
            raise APIError("TOO_LARGE", "请求体过大", 413)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise APIError("BAD_JSON", "请求体不是合法 JSON") from exc

    def _send_json(self, data, status=200, extra_headers=None):
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(raw)
        except BrokenPipeError:
            pass

    def _send_error(self, exc):
        if isinstance(exc, APIError):
            self._send_json({"error": {"code": exc.code, "message": exc.message}}, exc.status)
        else:
            if getattr(self.server, "logger", None):
                self.server.logger.exception("unhandled API error")
            self._send_json({"error": {"code": "INTERNAL", "message": "服务器内部错误"}}, 500)

    def _check(self, mutating):
        if not self._host_ok():
            raise APIError("FORBIDDEN", "只允许本机访问", 403)
        if not self._origin_ok():
            raise APIError("FORBIDDEN", "Origin 不被允许", 403)
        if mutating and not self._csrf_ok():
            raise APIError("CSRF", "CSRF 校验失败，请刷新页面", 403)

    def _api(self, method):
        try:
            self._check(method not in ("GET", "HEAD"))
            parsed = urllib.parse.urlsplit(self.path)
            query = _query_dict(parsed.query)
            body = self._read_body() if method in ("POST", "PUT", "DELETE") else {}
            if method == "POST" and parsed.path == "/api/chat":
                gen = self.server.api.chat_events(body)
                self._stream(gen)
                return
            result = _route(self.server.api, method, parsed.path, query, body)
            headers = None
            if parsed.path == "/api/export":
                headers = {"Content-Disposition": 'attachment; filename="acg-radar-export.json"'}
            self._send_json(result, extra_headers=headers)
        except Exception as exc:
            self._send_error(exc)

    def _stream(self, generator):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self._security_headers()
        self.end_headers()

        def chunk(data):
            raw = data.encode("utf-8") if isinstance(data, str) else data
            self.wfile.write(("%X\r\n" % len(raw)).encode("ascii") + raw + b"\r\n")

        try:
            for event, data in generator:
                payload = "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                chunk(payload)
            chunk(b"")
        except (BrokenPipeError, ConnectionResetError):
            try:
                generator.close()
            except Exception:
                pass
        except Exception as exc:
            if getattr(self.server, "logger", None):
                self.server.logger.exception("stream failed")
            try:
                payload = "event: error\ndata: " + json.dumps({"code": "STREAM_ERROR", "message": str(exc)}, ensure_ascii=False) + "\n\n"
                chunk(payload)
                chunk(b"")
            except Exception:
                pass
        finally:
            try:
                generator.close()
            except Exception:
                pass

    def _serve_static(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path in ("/", ""):
            path = "/index.html"
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        target = (WEB_DIR / path.lstrip("/")).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except Exception:
            raise APIError("NOT_FOUND", "文件不存在", 404)
        if not target.is_file():
            raise APIError("NOT_FOUND", "文件不存在", 404)
        content = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", (mime or "application/octet-stream") + ("; charset=utf-8" if mime and mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        try:
            self.wfile.write(content)
        except BrokenPipeError:
            pass

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            self._api("GET")
        else:
            try:
                self._check(False)
                self._serve_static()
            except Exception as exc:
                self._send_error(exc)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self._api("POST")

    def do_PUT(self):
        self._api("PUT")

    def do_DELETE(self):
        self._api("DELETE")

    def do_OPTIONS(self):
        self.send_response(405)
        self.send_header("Allow", "GET, POST, PUT, DELETE")
        self.end_headers()


def serve(app, host="127.0.0.1", port=8765, open_browser=False):
    api = API(app)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.app = app
    httpd.api = api
    httpd.logger = app.logger
    url = "http://" + host + ":" + str(httpd.server_address[1])
    print("ACG Radar is running at " + url, flush=True)
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
    finally:
        try:
            app.crawler.pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        httpd.shutdown()
        httpd.server_close()
