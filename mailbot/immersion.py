from __future__ import annotations
from bs4 import BeautifulSoup, NavigableString
from copy import deepcopy
from premailer import transform as inline_css

# Immersive translation inspired by old-immersive-translate
# 1) Detect main content container (by words coverage)
# 2) Collect block nodes (p/li/h2/h3) in document order with strict filtering
# 3) Clone each node, replace text nodes with translated text (preserve inline structure)
# 4) Insert clone right after original, mark as notranslate to avoid re-processing

MARK_ATTR = "data-translationmark"
MARK_ORIGINAL_DISPLAY = "data-translationoriginaldisplay"
NOTRANSLATE_CLASS = "notranslate"
# Balanced scope: include sub-headings but avoid h1/hero
BLOCK_TAGS = {"p", "li", "h2", "h3"}


def _has_ancestor_with_keywords(tag, keywords: tuple[str, ...]) -> bool:
    cur = tag
    depth = 0
    while cur and depth < 20:
        classes = " ".join(cur.get("class", [])).lower()
        idv = str(cur.get("id", "")).lower()
        role = (cur.get("role") or "").lower()
        blob = f"{classes} {idv}".strip()
        if any(k in blob for k in keywords) or role in ("banner", "navigation", "contentinfo"):
            return True
        cur = cur.parent
        depth += 1
    return False


def _ancestor_has_colored_bg(tag) -> bool:
    # Heuristic: hero/banner often have non-white backgrounds
    cur = tag.parent
    depth = 0
    while cur is not None and depth < 20:
        bgc = (cur.get('bgcolor') or '').strip().lower()
        if bgc and bgc not in ('#fff', '#ffffff', 'white', 'transparent', 'none'):
            return True
        style = (cur.get('style') or '').lower()
        if 'background' in style or 'background-color' in style:
            val = style
            # any non-white/transparent background
            if not any(y in val for y in ('#fff', '#ffffff', 'white', 'transparent')):
                return True
            if any(x in val for x in ('#000', '#111', '#222', '#333', '#444', '#555', '#666', '#777', 'black')):
                return True
            if 'rgb(' in val and 'transparent' not in val and ('255, 255, 255' not in val and '255,255,255' not in val):
                return True
        if cur.name in ('table', 'td') and (bgc or ('background' in (cur.get('style') or '').lower())):
            return True
        cur = cur.parent
        depth += 1
    return False


def _is_valid_node(tag) -> bool:
    if not getattr(tag, 'name', None):
        return False
    if tag.get(MARK_ATTR) == 'copiedNode':
        return False
    if tag.find_parent(attrs={MARK_ATTR: 'copiedNode'}):
        return False
    if tag.has_attr('translate') and str(tag.get('translate')).lower() == 'no':
        return False
    classes = set(tag.get('class', []) or [])
    if NOTRANSLATE_CLASS in classes:
        return False
    if tag.find_parent('blockquote'):
        return False
    # avoid heavy/non-textual
    if tag.find(["script", "style", "textarea", "svg", "iframe", "video", "form", "button"]):
        return False
    # header/footer/nav/legal etc.
    if _has_ancestor_with_keywords(tag, (
        "header", "footer", "nav", "menu", "banner", "masthead", "logo", "brand",
        "unsubscribe", "privacy", "copyright", "legal", "terms", "support", "help",
        "social", "share"
    )):
        return False
    # hero-like background
    if _ancestor_has_colored_bg(tag):
        return False
    # image-only paragraph heuristic
    if tag.name.lower() == 'p' and tag.find('img') and len(list(tag.children)) < 3:
        inner = tag.get_text(strip=True)
        if len(inner) < 80:
            return False
    # final text check
    txt = tag.get_text(" ", strip=True)
    return bool(txt) and len(txt) >= 3


def _find_content_container(soup: BeautifulSoup):
    body = soup.body or soup
    total_words = len((body.get_text(" ", strip=True) or "").split()) or 1
    ps = body.find_all("p")
    if not ps:
        ps = body.find_all("div")
    best = None
    best_words = 0
    for p in ps:
        txt = p.get_text(" ", strip=True)
        wc = len(txt.split()) if txt else 0
        if wc > best_words:
            best, best_words = p, wc
    if not best:
        return [body]
    cur = best
    covered = best_words
    steps = 0
    while cur and cur is not body and steps < 10:
        parent = cur.parent
        if not parent or not getattr(parent, 'get_text', None):
            break
        covered = len((parent.get_text(" ", strip=True) or "").split()) or covered
        if covered / total_words >= 0.35:
            cur = parent
            break
        cur = parent
        steps += 1
    return [cur or body]


def _collect_candidates(root) -> list:
    # document order
    picked = []
    picked_set = set()
    for el in root.descendants:
        if not getattr(el, 'name', None):
            continue
        if el.name.lower() not in BLOCK_TAGS:
            continue
        if not _is_valid_node(el):
            continue
        # skip if ancestor already picked
        anc = el.parent
        dup = False
        while anc is not None:
            if id(anc) in picked_set:
                dup = True
                break
            anc = getattr(anc, 'parent', None)
        if dup:
            continue
        picked.append(el)
        picked_set.add(id(el))
    return picked


def _collect_text_nodes(node):
    nodes = []
    for ch in node.descendants:
        if isinstance(ch, NavigableString):
            s = str(ch)
            if s and s.strip():
                nodes.append(ch)
    return nodes


def _replace_clone_text_preserving_structure(clone, translated: str):
    tnodes = _collect_text_nodes(clone)
    if not tnodes:
        return
    tr = translated or ''
    if len(tnodes) == 1:
        tnodes[0].replace_with(tr)
        return
    orig_texts = [str(n) for n in tnodes]
    lengths = [len(s) for s in orig_texts]
    total = sum(lengths) or 1
    # proportional distribution
    sizes = [max(0, int(round(len(tr) * L / total))) for L in lengths]
    diff = len(tr) - sum(sizes)
    if diff != 0:
        sizes[0] = max(0, sizes[0] + diff)
    pos = 0
    for i, n in enumerate(tnodes):
        k = sizes[i]
        chunk = tr[pos:pos+k]
        pos += k
        n.replace_with(chunk)


def inject_bilingual_html(html: str, translate_batch):
    # inline css first to preserve styles across mail clients
    soup = BeautifulSoup(inline_css(html), "html5lib")
    # detect main content container(s)
    roots = _find_content_container(soup)
    # collect candidates
    cands = []
    for r in roots:
        cands.extend(_collect_candidates(r))
    if not cands:
        return str(soup)

    # build segments
    segs = []
    useful = []
    for el in cands:
        txt = el.get_text(" ", strip=True)
        if txt:
            segs.append(txt)
            useful.append(el)
    if not segs:
        return str(soup)

    outs = translate_batch(segs)
    if not outs:
        return str(soup)

    # inject clones
    for el, tr in zip(useful, outs):
        clone = deepcopy(el)
        clone[MARK_ATTR] = 'copiedNode'
        # mark as notranslate to avoid further processing
        cls = set(clone.get('class', []) or [])
        cls.add(NOTRANSLATE_CLASS)
        clone['class'] = list(cls)
        # preserve display (optional)
        if el.has_attr('style'):
            clone[MARK_ORIGINAL_DISPLAY] = el.get('style')
        # lightweight style tweak
        style = clone.get('style', '') or ''
        if 'color:' not in style:
            style = (style + '; color:#0B6;').strip(';')
        clone['style'] = style
        _replace_clone_text_preserving_structure(clone, tr or '')
        el.insert_after(clone)
    return str(soup)
