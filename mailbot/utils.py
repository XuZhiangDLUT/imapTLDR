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
