"""Разбиение текста на чанки по токенам (tiktoken). Без I/O."""

from __future__ import annotations

import logging
from typing import List

import tiktoken

logger = logging.getLogger(__name__)


def get_encoder(encoding_name: str):
    try:
        return tiktoken.get_encoding(encoding_name)
    except KeyError:
        logger.warning("Encoding %s not found, using cl100k_base", encoding_name)
        return tiktoken.get_encoding("cl100k_base")


def chunk_text_by_tokens(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    encoding_name: str = "cl100k_base",
) -> List[str]:
    """
    Делит текст на чанки длиной ``chunk_size`` токенов с перекрытием ``overlap``.
    Пустая строка → [].
    """
    if not text or not str(text).strip():
        return []

    enc = get_encoder(encoding_name)
    tokens = enc.encode(text)
    if not tokens:
        return []

    size = max(1, chunk_size)
    ov = max(0, min(overlap, size - 1))
    step = max(1, size - ov)

    chunks: List[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        piece = tokens[start:end]
        decoded = enc.decode(piece)
        if decoded.strip():
            chunks.append(decoded)
        if end >= len(tokens):
            break
        start += step

    return chunks


def shingles(text: str, n: int = 8) -> set[str]:
    words = (text or "").casefold().split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard_shingles(a: str, b: str, n: int = 8) -> float:
    sa, sb = shingles(a, n), shingles(b, n)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


_SENTENCE_SPLIT_RE = __import__("re").compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = _SENTENCE_SPLIT_RE.split(raw)
    return [p.strip() for p in parts if p.strip()]


def pack_sentences(
    sentences: List[str],
    *,
    chunk_size: int = 600,
    overlap_sentences: int = 2,
    encoding_name: str = "cl100k_base",
) -> List[str]:
    """Упаковывает предложения в чанки ~chunk_size токенов без разрыва предложения."""
    if not sentences:
        return []
    enc = get_encoder(encoding_name)
    chunks: List[str] = []
    buf: List[str] = []
    buf_tokens = 0
    size = max(80, int(chunk_size))
    ov = max(0, int(overlap_sentences))

    def _flush() -> None:
        nonlocal buf, buf_tokens
        if not buf:
            return
        chunks.append(" ".join(buf).strip())
        if ov and len(buf) > ov:
            buf = buf[-ov:]
            buf_tokens = len(enc.encode(" ".join(buf)))
        else:
            buf = []
            buf_tokens = 0

    for sent in sentences:
        n = len(enc.encode(sent))
        if buf and buf_tokens + n > size:
            _flush()
        buf.append(sent)
        buf_tokens += n
        if buf_tokens >= size:
            _flush()
    if buf:
        chunks.append(" ".join(buf).strip())
    return [c for c in chunks if c]
