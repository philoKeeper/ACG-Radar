import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .config import VERSION


class AIError(Exception):
    def __init__(self, code, message, status=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _is_local(host):
    return host in ("localhost", "127.0.0.1", "::1") or (host or "").startswith("127.")


class AIClient:
    def __init__(self, settings, secrets, logger=None):
        ai = settings.get("ai", {})
        network = settings.get("network", {})
        self.base_url = (ai.get("base_url") or "").strip().rstrip("/")
        self.model = (ai.get("model") or "").strip()
        self.temperature = float(ai.get("temperature", 0.4) or 0)
        self.max_context_chars = int(ai.get("max_context_chars", 30000) or 30000)
        self.timeout = int(ai.get("timeout", 90) or 90)
        self.proxy = (network.get("proxy") or "").strip()
        self.key = secrets.get_key()
        self.secrets = secrets
        self.logger = logger
        self.last_usage = None

    @property
    def configured(self):
        if not self.base_url or not self.model:
            return False
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        return bool(self.key) or _is_local(host)

    def endpoint(self):
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def _opener(self):
        if self.proxy:
            host = urllib.parse.urlparse(self.base_url).hostname or ""
            if not _is_local(host):
                return urllib.request.build_opener(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        return urllib.request.build_opener()

    def _headers(self, stream=False):
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "ACGRadar/" + VERSION,
        }
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        return headers

    def _payload(self, messages, max_tokens=1400, stream=False):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": int(max_tokens),
            "stream": bool(stream),
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    def _usage_from(data):
        usage = (data or {}).get("usage") or {}
        if not isinstance(usage, dict):
            return None
        prompt = usage.get("prompt_tokens")
        if prompt is None:
            prompt = usage.get("input_tokens")
        completion = usage.get("completion_tokens")
        if completion is None:
            completion = usage.get("output_tokens")
        total = usage.get("total_tokens")
        def number(value):
            try:
                return max(0, int(value))
            except Exception:
                return None
        prompt = number(prompt)
        completion = number(completion)
        total = number(total)
        if prompt is None and completion is None and total is None:
            return None
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return {"prompt_tokens": prompt or 0, "completion_tokens": completion or 0, "total_tokens": total}

    def _request(self, messages, max_tokens, stream=False):
        if not self.configured:
            raise AIError("AI_NOT_CONFIGURED", "还没有配置可用的 AI API，请先在设置中填写 Base URL、模型和 Key。")
        data = json.dumps(self._payload(messages, max_tokens, stream), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint(), data=data, method="POST")
        for key, value in self._headers(stream).items():
            req.add_header(key, value)
        try:
            return self._opener().open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            message = body
            try:
                parsed = json.loads(body)
                message = (parsed.get("error") or {}).get("message") or body
            except Exception:
                pass
            if exc.code in (401, 403):
                raise AIError("AI_AUTH", "AI 认证失败（HTTP " + str(exc.code) + "）：" + message, exc.code) from exc
            if exc.code == 404:
                raise AIError("AI_MODEL", "AI 接口或模型不存在（HTTP 404）：" + message, exc.code) from exc
            if exc.code == 429:
                raise AIError("AI_RATE_LIMIT", "AI 服务限流（HTTP 429），请稍后再试。", exc.code) from exc
            raise AIError("AI_UPSTREAM", "AI 服务错误（HTTP " + str(exc.code) + "）：" + message, exc.code) from exc
        except Exception as exc:
            raise AIError("AI_NETWORK", "无法连接 AI 服务：" + str(exc)) from exc

    def chat(self, messages, max_tokens=1400):
        self.last_usage = None
        resp = self._request(messages, max_tokens, False)
        try:
            raw = resp.read()
        finally:
            try:
                resp.close()
            except Exception:
                pass
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:
            raise AIError("AI_BAD_RESPONSE", "AI 返回了无法解析的响应") from exc
        self.last_usage = self._usage_from(data)
        return self._extract_content(data)

    def _extract_content(self, data):
        try:
            choice = (data.get("choices") or [])[0]
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            return (content or "").strip()
        except Exception as exc:
            raise AIError("AI_BAD_RESPONSE", "AI 响应缺少 choices/message") from exc

    def stream_chat(self, messages, max_tokens=1400):
        self.last_usage = None
        resp = self._request(messages, max_tokens, True)
        content_type = (resp.headers.get("Content-Type") or "").lower()
        try:
            if "text/event-stream" not in content_type:
                raw = resp.read()
                try:
                    data = json.loads(raw.decode("utf-8", "replace"))
                    text = self._extract_content(data)
                    self.last_usage = self._usage_from(data)
                except AIError:
                    raise
                except Exception as exc:
                    raise AIError("AI_BAD_RESPONSE", "AI 返回了无法解析的响应") from exc
                if text:
                    yield text
                return
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except Exception:
                    continue
                usage = self._usage_from(data)
                if usage:
                    self.last_usage = usage
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, list):
                    piece = "".join(part.get("text", "") for part in piece if isinstance(part, dict))
                if piece:
                    yield piece
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def test(self):
        text = self.chat([{"role": "user", "content": "只回复 OK"}], max_tokens=10)
        return {"ok": True, "message": text[:100], "model": self.model, "endpoint": self.endpoint()}


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^\x60\x60\x60(?:json)?", "", text, flags=re.I).strip()
    text = re.sub(r"\x60\x60\x60$", "", text).strip()
    start = text.find("{")
    if start < 0:
        start = text.find("[")
    if start < 0:
        raise ValueError("没有找到 JSON")
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:index + 1])
    raise ValueError("JSON 不完整")
