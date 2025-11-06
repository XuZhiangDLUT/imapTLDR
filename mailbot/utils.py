from __future__ import annotations
from email.header import decode_header
from email.message import Message
from typing import Iterable


def decode_subject(msg: Message) -> str:
    raw = msg.get('Subject', '')
    parts = decode_header(raw)
    out = ''
    for s, enc in parts:
        if isinstance(s, bytes):
            try:
                out += s.decode(enc or 'utf-8', errors='replace')
            except Exception:
                out += s.decode('utf-8', errors='replace')
        else:
            out += s
    return out


def pass_prefix(subject: str, excluded_prefixes: Iterable[str]) -> bool:
    if not subject:
        return True
    for p in excluded_prefixes:
        if p and p in subject:
            return False
    return True


def split_by_chars(text: str, limit: int) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        out.append(text[i:i+limit])
        i += limit
    return out


def rough_token_count(text: str) -> int:
    """Heuristic token estimator.
    - If mostly ASCII, assume ~4 chars per token
    - Otherwise (CJK-heavy), assume ~1 char per token
    """
    if not text:
        return 0
    try:
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        ratio = ascii_chars / max(1, len(text))
    except Exception:
        ratio = 0.0
    if ratio >= 0.7:
        return max(1, int(round(len(text) / 4)))
    return len(text)
