from __future__ import annotations
from bs4 import BeautifulSoup, NavigableString
from copy import deepcopy
from premailer import transform as inline_css

# clone-and-replace strategy to preserve structure and inline styles
BLOCK_SEL = "p, li, div, td, h1, h2, h3, h4, h5, h6"


def _collect_text_nodes(node):
    q, nodes = [node], []
    while q:
        cur = q.pop(0)
        for ch in getattr(cur, "children", []):
            if isinstance(ch, NavigableString):
                if str(ch).strip():
                    nodes.append(ch)
            else:
                q.append(ch)
    return nodes


def inject_bilingual_html(html: str, translate_batch):
    # inline css first to preserve styles across mail clients
    soup = BeautifulSoup(inline_css(html), "html5lib")
    # skip historical quotes
    blocks = [b for b in soup.select(BLOCK_SEL) if not b.find_parent("blockquote")]
    segs = []
    for b in blocks:
        txt = "\n".join(n for n in b.get_text("\n", strip=True).splitlines() if n)
        if txt:
            segs.append(txt)
        else:
            segs.append("")
    if not any(segs):
        return str(soup)
    zh = translate_batch([s for s in segs if s])

    k = 0
    for b in blocks:
        txt = b.get_text(" ", strip=True)
        if not txt:
            continue
        if k >= len(zh):
            break
        clone = deepcopy(b)
        nodes = _collect_text_nodes(clone)
        lines = zh[k].splitlines()
        for i, n in enumerate(nodes):
            n.replace_with(lines[i] if i < len(lines) else "")
        # insert a <br> and the translated clone after original
        b.insert_after(clone)
        b.insert_after(soup.new_tag("br"))
        k += 1
    return str(soup)
