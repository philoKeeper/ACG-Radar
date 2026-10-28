import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone

CJK_RANGES = r"\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp(value, low, high):
    try:
        return max(low, min(high, value))
    except Exception:
        return low


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def clean_text(text, limit=20000) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()[:limit]


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", str(title or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff") or ("\u3040" <= ch <= "\u30ff"))


def parse_year(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        year = int(value)
        return year if 1900 <= year <= 2100 else None
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def parse_date(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
        except Exception:
            return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except Exception:
            continue
    return text[:40]


def guess_language(text: str, default="zh") -> str:
    text = text or ""
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if kana > max(2, han * 0.15):
        return "ja"
    if han > max(4, latin * 0.3):
        return "zh"
    if latin > 5:
        return "en"
    return default


def sentiment_from_score(score, scale=10):
    if score is None:
        return "neutral"
    value = float(score) / (float(scale or 10)) * 10
    if value >= 7.0:
        return "positive"
    if value <= 4.0:
        return "negative"
    return "mixed"


def excerpt(text: str, length=480) -> str:
    text = (text or "").strip()
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "..."


def log1p_ratio(count, scale=100):
    try:
        return min(1.0, math.log1p(max(0, count)) / math.log1p(scale))
    except Exception:
        return 0.0


def url_ok(url: str) -> bool:
    return isinstance(url, str) and (url.startswith("https://") or url.startswith("http://"))
