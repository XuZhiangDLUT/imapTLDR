from __future__ import annotations
from bs4 import BeautifulSoup
from premailer import transform as inline_css

# Safer immersive injection: only target text blocks; avoid duplicating large containers
# Balanced: include p/li and sub-headings (h2/h3); still avoid h1/hero
BLOCK_SEL = "p, li, h2, h3"


def _has_ancestor_with_keywords(tag, keywords: tuple[str, ...]) -> bool:
    cur = tag
    depth = 0
    while cur and depth < 20:
        attrs = (" ".join(cur.get("class", [])).lower() + " " + str(cur.get("id", "")).lower()).strip()
        role = (cur.get("role") or "").lower()
        if any(k in attrs for k in keywords) or role in ("banner", "navigation", "contentinfo"):
            return True
        cur = cur.parent
        depth += 1
    return False


def _ancestor_has_colored_bg(tag) -> bool:
    # Heuristic: banner/hero regions often have non-white backgrounds
    cur = tag.parent
    depth = 0
    while cur is not None and depth < 20:
        # bgcolor attribute
        bgc = (cur.get('bgcolor') or '').strip().lower()
        if bgc and bgc not in ('#fff', '#ffffff', 'white', 'transparent', 'none'):
            return True
        # inline background style
        style = (cur.get('style') or '').lower()
        if 'background' in style or 'background-color' in style:
            val = style
            # treat any non-white, non-transparent background as colored
            if not any(y in val for y in ('#fff', '#ffffff', 'white', 'transparent')):
                return True
            # also catch darker named/hex/rgb forms
            if any(x in val for x in ('#000', '#111', '#222', '#333', '#444', '#555', '#666', '#777', 'black')):
                return True
            if 'rgb(' in val and 'transparent' not in val and ('255, 255, 255' not in val and '255,255,255' not in val):
                return True
        # tables with explicit background often form hero bars
        if cur.name in ('table', 'td') and (bgc or ('background' in (cur.get('style') or '').lower())):
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
    # Skip inside non-white background regions (likely hero/banner)
    if _ancestor_has_colored_bg(tag):
        return False
    style = (tag.get("style", "") or "").lower()
    # if tag itself has non-white/transparent background or white text, skip
    if ('background' in style or 'background-color' in style) and not any(x in style for x in ('white', '#fff', '#ffffff', 'transparent')):
        return False
    if 'color:' in style and any(x in style for x in ('#fff', '#ffffff', 'white', 'rgb(255,255,255)', 'rgb(255, 255, 255)')):
        return False
    if any(k in style for k in ("position:absolute", "position:fixed", "float:")):
        return False
    text = tag.get_text(" ", strip=True)
    if not text or len(text) < 6:
        return False
    low = text.lower()
    if any(w in low for w in ("unsubscribe", "privacy", "copyright", "all rights reserved", "terms and conditions")):
        return False
    return True


def _find_content_container(soup: BeautifulSoup):
    # Pick ancestor of paragraph with most words, then climb up until it covers ~35% of all words
    body = soup.body or soup
    total_words = len((body.get_text(" ", strip=True) or "").split()) or 1
    ps = body.find_all("p") or body.find_all("div")
    best = None; best_words = 0
    for p in ps:
        txt = p.get_text(" ", strip=True)
        wc = len(txt.split()) if txt else 0
        if wc > best_words:
            best, best_words = p, wc
    if not best:
        return [body]
    cur = best; covered = best_words; steps = 0
    while cur and cur is not body and steps < 10:
        parent = cur.parent
        if not parent or not getattr(parent, 'get_text', None):
            break
        covered = len((parent.get_text(" ", strip=True) or "").split()) or covered
        if covered/total_words >= 0.35:
            cur = parent; break
        cur = parent; steps += 1
    return [cur or body]


def inject_bilingual_html(html: str, translate_batch):
    # inline css first to preserve styles across mail clients
    soup = BeautifulSoup(inline_css(html), "html5lib")
    # collect textual blocks within detected main container(s)
    roots = _find_content_container(soup)
    blocks = []
    for root in roots:
        blocks.extend([b for b in root.select(BLOCK_SEL) if _is_textual_block(b)])
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
