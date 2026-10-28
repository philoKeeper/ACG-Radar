import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _first_review_id(text):
    match = re.search(r'<review id="R(\d+)"', text or "")
    if not match:
        match = re.search(r"\bR(\d+)\b", text or "")
    return "[R" + match.group(1) + "]" if match else "[R1]"



class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        stream = bool(body.get("stream"))
        prompt_text = "\n".join(str(message.get("content", "")) for message in (body.get("messages") or []))
        if not stream and "<catalog>" in prompt_text:
            ids = re.findall(r'<work id="(\d+)"', prompt_text)
            text = json.dumps({"scores": [
                {"work_id": int(work_id), "score": max(10, 95 - index), "reason": "mock"}
                for index, work_id in enumerate(ids)
            ]}, ensure_ascii=False)
        else:
            text = "这是模拟回答：" + _first_review_id(prompt_text)
        if not stream:
            payload = {
                "choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        for piece in [text[:4], text[4:]]:
            frame = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        usage_frame = {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
        self.wfile.write(("data: " + json.dumps(usage_frame) + "\n\n").encode("utf-8"))
        self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def start_mock(port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:" + str(server.server_address[1]) + "/v1"


if __name__ == "__main__":
    import time
    server, url = start_mock(8899)
    print("mock AI at " + url, flush=True)
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        server.shutdown()
