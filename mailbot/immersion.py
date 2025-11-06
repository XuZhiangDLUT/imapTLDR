from __future__ import annotations
from bs4 import BeautifulSoup
from premailer import transform as inline_css

# Safer immersive injection: only target text blocks; avoid duplicating large containers
BLOCK_SEL = "p, li, h1, h2, h3, h4, h5, h6"


def _has_ancestor_with_keywords(tag, keywords: tuple[str, ...]) -> bool:
    cur = tag
    depth = 0
    while cur and depth < 6:
        attrs = (" ".join(cur.get("class", [])).lower() + " " + str(cur.get("id", "")).lower()).strip()
        if any(k in attrs for k in keywords):
            return True
        cur = cur.parent
        depth += 1
    return False


def _is_textual_block(tag) -> bool:
    # Skip blocks that likely contain layout-heavy elements
    if tag.find_parent("blockquote"):
        return False
    if tag.find(["table", "img", "button", "form", "iframe", "video", "svg"]):
        return False
    # Skip if in header/footer/nav/menus or legal/unsubscribe areas
    if _has_ancestor_with_keywords(tag, (
        "header", "footer", "nav", "menu", "banner", "masthead", "logo", "brand",
        "unsubscribe", "privacy", "copyright", "legal", "terms", "support", "help",
        "social", "share"
    )):
        return False
    style = (tag.get("style", "") or "").lower()
    if any(k in style for k in ("position:absolute", "position:fixed", "float:")):
        return False
    text = tag.get_text(" ", strip=True)
    if not text or len(text) < 6:
        return False
    low = text.lower()
    if any(w in low for w in ("unsubscribe", "privacy", "copyright", "all rights reserved", "terms and conditions")):
        return False
    return True


def inject_bilingual_html(html: str, translate_batch):
    # inline css first to preserve styles across mail clients
    soup = BeautifulSoup(inline_css(html), "html5lib")
    # collect only textual blocks
    blocks = [b for b in soup.select(BLOCK_SEL) if _is_textual_block(b)]
    segs: list[str] = []
    for b in blocks:
        txt = "\n".join(n for n in b.get_text("\n", strip=True).splitlines() if n)
        segs.append(txt)
    if not any(segs):
        return str(soup)

    translations = translate_batch([s for s in segs if s])
    k = 0
    for b, s in zip(blocks, segs):
        if not s:
            continue
        if k >= len(translations):
            break
        tr = translations[k]
        k += 1
        # insert translated line after the original as a simple div
        ins = soup.new_tag("div")
        ins.string = tr
        ins["style"] = (
            "color:#0B6; margin-top:4px; display:block; line-height:1.45; font-size:0.95em;"
            "word-break:break-word; white-space:normal;"
        )
        b.insert_after(ins)
    return str(soup)
