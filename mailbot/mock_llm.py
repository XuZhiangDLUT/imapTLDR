from __future__ import annotations
from typing import Iterable



def summarize_mock(text: str, max_bullets: int = 5) -> str:
    t = (text or "").strip()
    if not t:
        return "(empty)"
    # naive split by sentence enders
    import re
    parts = [p.strip() for p in re.split(r"[。.!?\n]", t) if p.strip()]
    bullets = parts[:max_bullets]
    return "\n".join(f"- {b}" for b in bullets)
