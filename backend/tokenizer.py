import re
import unicodedata

CJK = r"\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[" + CJK + r"]+")
STOP = {
    "的了", "了。", "这部", "那部", "一个", "什么", "怎么", "可以", "觉得", "非常",
    "但是", "因为", "所以", "而且", "还是", "就是", "没有", "我们", "你们", "他们",
    "自己", "时候", "已经", "这个", "那个", "这部", "一部", "有点", "真的", "感觉",
    "the", "and", "for", "with", "this", "that", "is", "are", "was", "were",
}


def _parts(text: str):
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return TOKEN_RE.findall(text)


def tokenize(text: str, for_query=False):
    tokens = []
    for part in _parts(text):
        if re.match(r"[a-z0-9_]", part):
            if for_query and len(part) < 2:
                continue
            tokens.append(part)
            continue
        if len(part) == 1:
            tokens.append(part)
        else:
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
    seen = set()
    result = []
    for token in tokens:
        if token in STOP:
            continue
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def query_tokens(text: str, limit=28):
    return tokenize(text, for_query=True)[:limit]


def fts_query(text_or_tokens):
    tokens = text_or_tokens if isinstance(text_or_tokens, (list, tuple)) else query_tokens(text_or_tokens)
    tokens = [t for t in tokens if t][:28]
    if not tokens:
        return ""
    return " OR ".join('"' + t.replace('"', '') + '"' for t in tokens)


def chunk_text(text: str, max_chars=1050, overlap=120, max_chunks=20):
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    units = []
    for para in paragraphs:
        if len(para) <= max_chars:
            units.append(para)
            continue
        sentences = re.split(r"(?<=[。！？!?；;])", para)
        buf = ""
        for sent in sentences:
            if not sent:
                continue
            if len(sent) > max_chars:
                if buf:
                    units.append(buf)
                    buf = ""
                start = 0
                while start < len(sent):
                    units.append(sent[start:start + max_chars])
                    start += max_chars - overlap
            elif len(buf) + len(sent) <= max_chars:
                buf += sent
            else:
                if buf:
                    units.append(buf)
                buf = sent
        if buf:
            units.append(buf)
    chunks = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current += "\n" + unit
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap and len(current) > overlap else ""
            if tail and len(tail) + 1 + len(unit) <= max_chars:
                current = (tail + "\n" + unit).strip()
            else:
                current = unit
        if len(chunks) >= max_chunks:
            break
    if current and len(chunks) < max_chunks:
        chunks.append(current)
    return [c.strip() for c in chunks if c.strip()][:max_chunks]
