import gzip
import json
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

from .config import VERSION
from .util import clean_text

USER_AGENT = "ACGRadar/" + VERSION + " (personal comment analysis tool; local use)"
MAX_BYTES = 8 * 1024 * 1024


class FetchError(Exception):
    def __init__(self, code, message, status=None, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.last = {}

    def wait(self, host, interval):
        interval = max(0.2, float(interval or 1.0))
        with self.lock:
            now = time.monotonic()
            last = self.last.get(host, 0)
            delay = interval - (now - last)
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self.last[host] = now


def _is_local(host):
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


class HttpClient:
    def __init__(self, proxy="", timeout=30, interval=1.0, logger=None, retries=3):
        self.proxy = (proxy or "").strip()
        self.timeout = timeout
        self.interval = interval
        self.retries = retries
        self.logger = logger
        self.limiter = RateLimiter()
        self.sem = threading.Semaphore(2)
        self.direct = urllib.request.build_opener()
        self.proxied = urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": self.proxy,
            "https": self.proxy,
        })) if self.proxy else self.direct

    def _opener(self, url):
        host = urllib.parse.urlparse(url).hostname or ""
        if self.proxy and not _is_local(host):
            return self.proxied
        return self.direct

    def _request(self, url, method="GET", payload=None, headers=None, retries=3):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise FetchError("BAD_URL", "仅支持 HTTP/HTTPS 地址")
        host = parsed.hostname or ""
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error = None
        for attempt in range(retries + 1):
            self.limiter.wait(host, self.interval)
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("User-Agent", USER_AGENT)
            req.add_header("Accept", "application/json, text/plain, */*")
            req.add_header("Accept-Encoding", "gzip")
            if body is not None:
                req.add_header("Content-Type", "application/json")
            for key, value in (headers or {}).items():
                req.add_header(key, value)
            try:
                with self.sem:
                    resp = self._opener(url).open(req, timeout=self.timeout)
                    raw = resp.read(MAX_BYTES + 1)
                    status = getattr(resp, "status", 200)
                    encoding = (resp.headers.get("Content-Encoding") or "").lower()
            except urllib.error.HTTPError as exc:
                raw = b""
                try:
                    raw = exc.read(MAX_BYTES)
                except Exception:
                    pass
                status = exc.code
                retryable = status == 429 or 500 <= status < 600
                if retryable and attempt < retries:
                    delay = self._retry_delay(exc.headers.get("Retry-After"), attempt)
                    if self.logger:
                        self.logger.warning("HTTP %s from %s, retry in %.1fs", status, host, delay)
                    time.sleep(delay)
                    continue
                text = raw.decode("utf-8", "replace")[:300]
                raise FetchError("HTTP_" + str(status), "来源返回 HTTP " + str(status) + ("：" + text if text else ""), status, retryable) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < retries:
                    delay = self._retry_delay(None, attempt)
                    if self.logger:
                        self.logger.warning("Network error from %s, retry in %.1fs: %s", host, delay, exc)
                    time.sleep(delay)
                    continue
                raise FetchError("NETWORK", "网络请求失败：" + str(exc), None, True) from exc
            if len(raw) > MAX_BYTES:
                raise FetchError("TOO_LARGE", "响应过大，已中止")
            if encoding == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception as exc:
                    raise FetchError("DECODE", "响应解压失败") from exc
            return status, raw, dict(resp.headers)
        raise FetchError("NETWORK", "网络请求失败：" + str(last_error), None, True)

    def _retry_delay(self, retry_after, attempt):
        if retry_after:
            try:
                return min(20.0, max(0.5, float(retry_after)))
            except Exception:
                try:
                    dt = parsedate_to_datetime(retry_after)
                    return min(20.0, max(0.5, dt.timestamp() - time.time()))
                except Exception:
                    pass
        return min(8.0, (0.8 * (2 ** attempt)) + random.random() * 0.4)

    def get_text(self, url, headers=None, retries=None):
        _, raw, _ = self._request(url, "GET", None, headers, self.retries if retries is None else retries)
        return raw.decode("utf-8", "replace")

    def get_json(self, url, headers=None, retries=None):
        _, raw, _ = self._request(url, "GET", None, headers, self.retries if retries is None else retries)
        return self._decode_json(raw)

    def post_json(self, url, payload, headers=None, retries=None):
        _, raw, _ = self._request(url, "POST", payload, headers, self.retries if retries is None else retries)
        return self._decode_json(raw)

    def _decode_json(self, raw):
        text = raw.decode("utf-8", "replace").lstrip()
        if not text:
            raise FetchError("EMPTY", "来源返回空响应")
        if text[:1] == "<":
            raise FetchError("HTML", "来源返回 HTML，可能被反爬或接口已变更")
        try:
            return json.loads(text)
        except Exception as exc:
            raise FetchError("BAD_JSON", "无法解析来源 JSON：" + clean_text(text[:120], 120)) from exc
