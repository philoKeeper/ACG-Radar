import copy
import json
import logging
import logging.handlers
import os
import re
import sys
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
VERSION = "1.0.1"

DEFAULTS = {
    "ai": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "temperature": 0.4,
        "max_context_chars": 30000,
        "timeout": 90,
        "json_mode": False,
    },
    "crawler": {
        "review_limit": 120,
        "max_reviews": 500,
        "allow_nsfw": True,
        "request_interval": 0.5,
    },
    "network": {
        "proxy": "",
    },
}

SOURCE_WEIGHTS = {
    "bangumi": 1.0,
    "demo": 0.8,
}

SOURCE_LABELS = {
    "bangumi": "Bangumi",
    "demo": "演示",
}


def data_dir() -> Path:
    raw = os.environ.get("ACG_RADAR_DATA", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_DATA_DIR


def ensure_dirs(base: Path | None = None) -> Path:
    path = Path(base) if base else data_dir()
    for name in ("", "cache", "logs", "backups"):
        (path / name).mkdir(parents=True, exist_ok=True)
    return path


def deep_merge(base, patch):
    result = copy.deepcopy(base)
    if not isinstance(patch, dict):
        return result
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_settings(conn) -> dict:
    stored = {}
    try:
        rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    except Exception:
        rows = []
    for row in rows:
        try:
            stored[row["key"]] = json.loads(row["value_json"])
        except Exception:
            continue
    return deep_merge(DEFAULTS, stored)


def save_settings(conn, patch) -> dict:
    current = get_settings(conn)
    merged = deep_merge(current, patch or {})
    for key in ("ai", "crawler", "network"):
        conn.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (key, json.dumps(merged.get(key, {}), ensure_ascii=False), now_iso()),
        )
    conn.commit()
    return merged


class SecretStore:
    def __init__(self, base: Path | None = None):
        self.base = ensure_dirs(base)
        self.path = self.base / "secrets.json"

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get_key(self) -> str:
        env = os.environ.get("ACG_AI_API_KEY", "").strip()
        if env:
            return env
        return str(self._read().get("api_key", "") or "").strip()

    def source(self) -> str:
        if os.environ.get("ACG_AI_API_KEY", "").strip():
            return "env"
        return "file" if self._read().get("api_key") else "none"

    def set_key(self, key: str) -> None:
        key = (key or "").strip()
        data = self._read()
        data["api_key"] = key
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def clear(self) -> None:
        data = self._read()
        data.pop("api_key", None)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def hint(self) -> str:
        key = self.get_key()
        if not key:
            return ""
        if len(key) <= 8:
            return "****"
        return key[:3] + "..." + key[-4:]

    def get_bangumi_token(self) -> str:
        env = os.environ.get("ACG_BANGUMI_TOKEN", "").strip()
        if env:
            return env
        return str(self._read().get("bangumi_token", "") or "").strip()

    def get_bangumi_cookie(self) -> str:
        env = os.environ.get("ACG_BANGUMI_COOKIE", "").strip()
        if env:
            return env
        return str(self._read().get("bangumi_cookie", "") or "").strip()

    def set_bangumi(self, token=None, cookie=None) -> None:
        data = self._read()
        if token is not None:
            data["bangumi_token"] = (token or "").strip()
        if cookie is not None:
            data["bangumi_cookie"] = (cookie or "").strip()
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def clear_bangumi(self, token=False, cookie=False) -> None:
        data = self._read()
        if token:
            data.pop("bangumi_token", None)
        if cookie:
            data.pop("bangumi_cookie", None)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def bangumi_hint(self) -> str:
        token = self.get_bangumi_token()
        if not token:
            return ""
        return "****" if len(token) <= 8 else token[:3] + "..." + token[-4:]


def public_settings(conn, secrets: SecretStore) -> dict:
    settings = get_settings(conn)
    ai = dict(settings.get("ai", {}))
    ai.pop("api_key", None)
    ai["key_configured"] = bool(secrets.get_key())
    ai["key_hint"] = secrets.hint()
    ai["key_source"] = secrets.source()
    host = urllib.parse.urlparse(ai.get("base_url") or "").hostname or ""
    local = host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")
    ai["configured"] = bool(ai.get("base_url") and ai.get("model") and (secrets.get_key() or local))
    return {
        "ai": ai,
        "crawler": settings.get("crawler", {}),
        "network": settings.get("network", {}),
        "bangumi": {
            "token_configured": bool(secrets.get_bangumi_token()),
            "token_hint": secrets.bangumi_hint(),
            "cookie_configured": bool(secrets.get_bangumi_cookie()),
        },
        "sources": SOURCE_LABELS,
        "version": VERSION,
    }


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RedactFilter(logging.Filter):
    pattern = re.compile(
        r"(Bearer\s+)[A-Za-z0-9._\-]+|(api_key\s*[=:]\s*)[^\s,;]+|(proxy\s*[=:]\s*)[^\s,;]+|(cookie\s*[=:]\s*)[^\s,;]+",
        re.IGNORECASE,
    )

    def filter(self, record):
        try:
            msg = record.getMessage()
            record.msg = self.pattern.sub(lambda m: (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "") + "***", msg)
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging(base: Path | None = None, debug: bool = False) -> logging.Logger:
    path = ensure_dirs(base) / "logs" / "app.log"
    logger = logging.getLogger("acg_radar")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(RedactFilter())
        logger.addHandler(fh)
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.addFilter(RedactFilter())
    logger.addHandler(sh)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return logger
