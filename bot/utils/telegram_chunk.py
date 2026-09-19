"""Разбиение длинного HTML для Telegram (лимит 4096 символов на сообщение)."""

from __future__ import annotations

# Официальный лимит; запас под сущности.
TELEGRAM_MESSAGE_MAX_CHARS = 4096
TELEGRAM_HTML_CHUNK_SAFE = 4000


def split_telegram_html_chunks(
    html: str,
    max_len: int = TELEGRAM_HTML_CHUNK_SAFE,
) -> list[str]:
    """
    Делит текст на части ≤ max_len, стараясь резать по \\n\\n, \\n, пробелу, после ``>``.
    """
    text = "" if html is None else html
    if not text:
        return [" "]
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)

    while start < n:
        if n - start <= max_len:
            chunks.append(text[start:n])
            break

        window = text[start : start + max_len]
        rel = 0
        br = window.rfind("\n\n")
        if br >= max_len // 3:
            rel = br + 2
        else:
            br = window.rfind("\n")
            if br >= max_len // 3:
                rel = br + 1
            else:
                br = window.rfind(" ")
                if br >= max_len // 2:
                    rel = br + 1
                else:
                    br = window.rfind(">")
                    if br > max_len // 2:
                        rel = br + 1

        if rel <= 0:
            rel = max_len

        end = start + rel
        if end <= start:
            end = start + max_len
        chunks.append(text[start:end])
        start = end

    return chunks
