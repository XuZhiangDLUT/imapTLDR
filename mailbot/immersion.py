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
# Balanced scope: include sub-headings; broaden for newsletter/table layouts
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "ol", "ul"}
# Additional blocklike candidates to consider when they hold text directly
EXTRA_BLOCKLIKE = {"div", "section", "article"}


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


def _has_block_children(el) -> bool:
    if not getattr(el, 'find', None):
        return False
    # any nested block-level elements imply el is a container, not a leaf to translate
    try:
        return bool(el.find(list(BLOCK_TAGS)))
    except Exception:
        return False


def _collect_candidates(root) -> list:
    # document order
    picked = []
    picked_set = set()
    for el in root.descendants:
        if not getattr(el, 'name', None):
            continue
        name = el.name.lower()
        consider = False
        if name in BLOCK_TAGS:
            consider = True
        elif name in EXTRA_BLOCKLIKE:
            # pick div/section/article only when they are textual leaves (no block children)
            if not _has_block_children(el):
                consider = True
        else:
            consider = False
        if not consider:
            continue
        if not _is_valid_node(el):
            continue
        # ensure non-trivial text
        txt = el.get_text(" ", strip=True) or ""
        if len(txt) < 3:
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
        name = (el.name or '').lower()
        if name in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            # For headings/paragraphs, append a blocky span inside to stay on next line, avoid invalid div-in-p/heading
            holder = soup.new_tag('span')
            holder[MARK_ATTR] = 'copiedNode'
            cls = set(holder.get('class', []) or [])
            cls.add(NOTRANSLATE_CLASS)
            holder['class'] = list(cls)
            holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
            holder.string = tr or ''
            el.append(holder)
        elif name in ('td', 'th', 'li', 'div', 'section', 'article', 'blockquote'):
            # For containers/cells, append a div inside
            holder = soup.new_tag('div')
            holder[MARK_ATTR] = 'copiedNode'
            cls = set(holder.get('class', []) or [])
            cls.add(NOTRANSLATE_CLASS)
            holder['class'] = list(cls)
            holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
            holder.string = tr or ''
            el.append(holder)
        elif name == 'pre':
            # After pre blocks, insert sibling div (not inside <pre>)
            holder = soup.new_tag('div')
            holder[MARK_ATTR] = 'copiedNode'
            cls = set(holder.get('class', []) or [])
            cls.add(NOTRANSLATE_CLASS)
            holder['class'] = list(cls)
            holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
            holder.string = tr or ''
            el.insert_after(holder)
        else:
            clone = deepcopy(el)
            clone[MARK_ATTR] = 'copiedNode'
            # mark as notranslate to avoid further processing
            cls = set(clone.get('class', []) or [])
            cls.add(NOTRANSLATE_CLASS)
            clone['class'] = list(cls)
            # preserve display (optional)
            if el.has_attr('style'):
                clone[MARK_ORIGINAL_DISPLAY] = el.get('style')
            # preserve existing inline style as-is; do not change font/size/color
            _replace_clone_text_preserving_structure(clone, tr or '')
            el.insert_after(clone)
    return str(soup)

# --- Conservative fallback: line-by-line English detection ---
import re

def _looks_like_style_or_code(s: str) -> bool:
    # Heuristic: avoid CSS/HTML-like blobs in text nodes
    if not s:
        return False
    t = s.strip()
    if len(t) > 2 and t.lstrip().startswith(('/*','//','<!--')):
        return True
    # many semicolons/colons/braces -> looks like CSS/JSON/code
    punc = sum(t.count(ch) for ch in (';', ':', '{', '}', '<', '>', '/*', '*/'))
    if punc >= 4:
        return True
    # long camelCase tokens typical of code identifiers
    if re.search(r"[A-Za-z]+[A-Z][a-z]+", t) and len(t) > 50:
        return True
    return False

_ENG_RE = re.compile(r"[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://|www\\.")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
_UNIT_RE = re.compile(r"\\b(?:px|pt|em|rem|vh|vw|cm|mm|in)\\b", re.I)


def _is_probably_english_text(s: str) -> bool:
    if not s:
        return False
    t = s.strip()
    if len(t) < 2:
        return False
    if _looks_like_style_or_code(t):
        return False
    if not _ENG_RE.search(t):
        return False
    # ignore bare urls/emails
    if _URL_RE.search(t) and len(t) <= 60:
        return False
    if _EMAIL_RE.search(t) and len(t) <= 40:
        return False
    # ignore unit-only snippets
    if _UNIT_RE.search(t) and len(t) <= 8:
        return False
    return True

_CONSERVATIVE_TAGS = {
    # block-like and common containers
    'p','li','div','section','article','td','th','h1','h2','h3','h4','h5','h6','pre','blockquote','span','a'
}

_DEF_SKIP_PARENTS = { 'script','style','svg','form','button','noscript','textarea' }

def _has_translated_sibling(el) -> bool:
    try:
        nxt = el.next_sibling
        # skip over whitespace-only nodes
        while nxt is not None and isinstance(nxt, NavigableString) and not str(nxt).strip():
            nxt = nxt.next_sibling
        return getattr(nxt, 'get', lambda *a, **k: None)(MARK_ATTR) == 'copiedNode'
    except Exception:
        return False

def _nearest_block_container(el):
    names = set(['p','li','div','section','article','td','th','blockquote','pre','h1','h2','h3','h4','h5','h6'])
    cur = el
    steps = 0
    while cur is not None and steps < 12:
        try:
            n = (getattr(cur, 'name', None) or '').lower()
        except Exception:
            n = ''
        if n in names:
            return cur
        cur = getattr(cur, 'parent', None)
        steps += 1
    return None

def inject_bilingual_html_conservative(html: str, translate_batch):
    """
    Robust per-block injection: for typical text blocks (p/li/h*/td/th/div without nested blocks)
    append one next-line translation inside the same block.
    """
    soup = BeautifulSoup(inline_css(html), 'html5lib')
    elems = []
    picked_set = set()

    def consider(el) -> bool:
        if el.get(MARK_ATTR) == 'copiedNode' or el.find_parent(attrs={MARK_ATTR: 'copiedNode'}):
            return False
        if el.has_attr('translate') and str(el.get('translate')).lower() == 'no':
            return False
        clss = set(el.get('class', []) or [])
        if NOTRANSLATE_CLASS in clss:
            return False
        if el.find_parent('blockquote'):
            return False
        if el.find_parent(_DEF_SKIP_PARENTS) is not None:
            return False
        # avoid container-level blocks with nested blocks (use their child blocks instead)
        try:
            nm = (el.name or '').lower()
            if nm in ('div','section','article','td','th') and _has_block_children(el):
                return False
        except Exception:
            pass
        try:
            if el.find(attrs={MARK_ATTR: 'copiedNode'}):
                return False
        except Exception:
            pass
        txt = el.get_text(' ', strip=True) or ''
        if not _is_probably_english_text(txt):
            return False
        # if already has an injected sibling right after, skip
        if _has_translated_sibling(el):
            return False
        anc = el.parent
        while anc is not None:
            if id(anc) in picked_set:
                return False
            anc = getattr(anc, 'parent', None)
        return True

    tiers = [
        ['p','li','h1','h2','h3','h4','h5','h6','pre','blockquote'],
        ['div','section','article'],
        ['td','th'],
    ]

    seen = set()
    for tags in tiers:
        for el in soup.find_all(tags):
            if id(el) in seen:
                continue
            name = (el.name or '').lower()
            if name in ('td','th'):
                try:
                    if _has_block_children(el):
                        continue
                except Exception:
                    pass
            if not consider(el):
                continue
            elems.append(el)
            picked_set.add(id(el))
            seen.add(id(el))
    if not elems:
        return str(soup)

    segs = [e.get_text(' ', strip=True) for e in elems]
    outs = []
    CHUNK = 40
    for i in range(0, len(segs), CHUNK):
        part = translate_batch(segs[i:i+CHUNK])
        if not part:
            part = [''] * len(segs[i:i+CHUNK])
        outs.extend(part)
    if not outs:
        return str(soup)

    for el, tr in zip(elems, outs):
        try:
            name = (el.name or '').lower()
            if name in ('p','h1','h2','h3','h4','h5','h6'):
                # place as a sibling block to keep email-client layout robust
                holder = soup.new_tag('div')
                holder[MARK_ATTR] = 'copiedNode'
                cls = set(holder.get('class', []) or [])
                cls.add(NOTRANSLATE_CLASS)
                holder['class'] = list(cls)
                holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
                holder.string = tr or ''
                el.insert_after(holder)
            elif name in ('td','th','li','div','section','article','blockquote'):
                holder = soup.new_tag('div')
                holder[MARK_ATTR] = 'copiedNode'
                cls = set(holder.get('class', []) or [])
                cls.add(NOTRANSLATE_CLASS)
                holder['class'] = list(cls)
                holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
                holder.string = tr or ''
                el.append(holder)
            elif name == 'pre':
                holder = soup.new_tag('div')
                holder[MARK_ATTR] = 'copiedNode'
                cls = set(holder.get('class', []) or [])
                cls.add(NOTRANSLATE_CLASS)
                holder['class'] = list(cls)
                holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
                holder.string = tr or ''
                el.insert_after(holder)
            else:
                clone = deepcopy(el)
                clone[MARK_ATTR] = 'copiedNode'
                cls = set(clone.get('class', []) or [])
                cls.add(NOTRANSLATE_CLASS)
                clone['class'] = list(cls)
                if el.has_attr('style'):
                    clone[MARK_ORIGINAL_DISPLAY] = el.get('style')
                _replace_clone_text_preserving_structure(clone, tr or '')
                el.insert_after(clone)
        except Exception:
            continue
    return str(soup)

# Ultra-naive: per inline segment right-after placement
INLINE_TAGS = set(['a','span','em','strong','b','i','u','small','sup','sub','mark','code'])
BLOCK_CANDIDATES = set(['p','li','td','th','h1','h2','h3','h4','h5','h6','div','section','article','blockquote'])

def _is_inline_only(el) -> bool:
    try:
        return not _has_block_children(el)
    except Exception:
        return True

def inject_bilingual_html_linewise(html: str, translate_batch):
    """
    Simplest rule: iterate block containers, then iterate their inline/text children,
    whenever a child segment contains English, translate it and insert a new blocky span/div
    immediately after that segment (not at the end of the container).
    """
    soup = BeautifulSoup(inline_css(html), 'html5lib')
    segments = []  # list of (node, text, mode)

    def push_segment(node, text):
        if not _is_probably_english_text(text):
            return
        # skip if next sibling already our marker
        try:
            nxt = node.next_sibling
            while nxt is not None and isinstance(nxt, NavigableString) and not str(nxt).strip():
                nxt = nxt.next_sibling
            if getattr(nxt, 'get', lambda *a, **k: None)(MARK_ATTR) == 'copiedNode':
                return
        except Exception:
            pass
        segments.append((node, text))

    for el in soup.find_all(list(BLOCK_CANDIDATES)):
        # skip heavy areas / notranslate / already injected
        if el.get(MARK_ATTR) == 'copiedNode' or el.find_parent(attrs={MARK_ATTR:'copiedNode'}):
            continue
        if el.has_attr('translate') and str(el.get('translate')).lower() == 'no':
            continue
        clss = set(el.get('class', []) or [])
        if NOTRANSLATE_CLASS in clss:
            continue
        if el.find_parent('blockquote'):
            continue
        if el.find_parent(_DEF_SKIP_PARENTS) is not None:
            continue
        # Containers with nested blocks handled by their children; we only split pure inline containers
        if not _is_inline_only(el):
            continue
        # walk direct children as inline segments, break on <br>
        buf_text = ''
        buf_nodes = []
        def flush_buffer():
            if not buf_nodes:
                return
            text = ' '.join([n.get_text(' ', strip=True) if not isinstance(n, NavigableString) else str(n) for n in buf_nodes]).strip()
            if text:
                # place translation after the last node in buffer
                push_segment(buf_nodes[-1], text)
            buf_nodes.clear()
        for ch in el.contents:
            if getattr(ch, 'name', None) == 'br':
                flush_buffer()
                continue
            if isinstance(ch, NavigableString) or (getattr(ch, 'name', '').lower() in INLINE_TAGS):
                buf_nodes.append(ch)
            else:
                # unexpected child, flush current buffer
                flush_buffer()
        flush_buffer()

    if not segments:
        return str(soup)

    texts = [t for _, t in segments]
    outs = []
    CHUNK = 50
    for i in range(0, len(texts), CHUNK):
        part = translate_batch(texts[i:i+CHUNK])
        if not part:
            part = [''] * len(texts[i:i+CHUNK])
        outs.extend(part)

    for (node, _), tr in zip(segments, outs):
        try:
            # choose container tag based on ancestor block type
            parent = node.parent
            tname = (parent.name or '').lower() if hasattr(parent, 'name') else ''
            # for paragraph/heading containers, place a sibling div after the whole block
            if tname in ('p','h1','h2','h3','h4','h5','h6'):
                holder = soup.new_tag('div')
                holder[MARK_ATTR] = 'copiedNode'
                cls = set(holder.get('class', []) or [])
                cls.add(NOTRANSLATE_CLASS)
                holder['class'] = list(cls)
                holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
                holder.string = tr or ''
                parent.insert_after(holder)
            else:
                tagname = 'div'
                holder = soup.new_tag(tagname)
                holder[MARK_ATTR] = 'copiedNode'
                cls = set(holder.get('class', []) or [])
                cls.add(NOTRANSLATE_CLASS)
                holder['class'] = list(cls)
                holder['style'] = 'margin-top:6px;display:block;clear:both;font:inherit;color:inherit;line-height:inherit;'
                holder.string = tr or ''
                node.insert_after(holder)
        except Exception:
            continue

    return str(soup)


# In-place: translate text nodes only, keep DOM unchanged
def translate_html_inplace(html: str, translate_batch):
    """
    Translate visible text nodes "in-place" by keeping the original text
    and appending the translation right after it within the same location.
    - The translation is wrapped in an inline span with a vivid color, while
      inheriting other typography to avoid layout shifts.
    - Skips script/style/noscript/textarea/svg/form/button and <code>/<pre>
    - Splits each text node by newlines (keeps delimiters) and translates
      only parts that look like English
    - Between original and translation, insert exactly one space
    - Preserves surrounding whitespace and newlines exactly
    """
    soup = BeautifulSoup(inline_css(html), 'html5lib')

    # Build translation requests by scanning text nodes
    requests = []  # list[str]
    slots = []     # list of (text_node, token_index, leading, core, trailing)

    SKIP_PARENTS = set(['script','style','svg','form','button','noscript','textarea','code','pre'])

    for node in soup.descendants:
        if not isinstance(node, NavigableString):
            continue
        raw = str(node)
        if not raw or not raw.strip():
            continue
        # skip certain parents
        par = getattr(node, 'parent', None)
        pname = (getattr(par, 'name', None) or '').lower()
        if pname in SKIP_PARENTS:
            continue
        # avoid re-processing cloned/injected areas (not used in-place, but safe)
        try:
            if par and (par.get(MARK_ATTR) == 'copiedNode' or par.find_parent(attrs={MARK_ATTR:'copiedNode'})):
                continue
        except Exception:
            pass

        # tokenize by newline while keeping delimiters
        tokens = []
        i = 0
        import re as _re
        for part in _re.split(r'(\r?\n)', raw):
            tokens.append(part)
        # collect translatable parts (even indexes that are not newline tokens)
        for idx, tok in enumerate(tokens):
            if idx % 2 == 1 and tok in ('\n','\r\n'):
                continue
            if tok in ('\n','\r\n'):
                continue
            if not tok or not tok.strip():
                continue
            # strip but remember leading/trailing spaces
            m = _re.match(r'^(\s*)(.*?)(\s*)$', tok, _re.S)
            lead = m.group(1)
            core = m.group(2)
            tail = m.group(3)
            if not _is_probably_english_text(core):
                continue
            requests.append(core)
            slots.append((node, idx, lead, core, tail))

    if not requests:
        return str(soup)

    # Translate in chunks
    results = []
    CHUNK = 60
    for i in range(0, len(requests), CHUNK):
        part = translate_batch(requests[i:i+CHUNK])
        if not part:
            part = [''] * len(requests[i:i+CHUNK])
        results.extend(part)

    # Apply back to nodes
    # Build a mapping from text node to its token list to reconstruct once
    from collections import defaultdict
    import re as _re2
    # Group slots per node
    per_node = defaultdict(list)
    rpos = 0
    for (node, token_index, lead, core, tail) in slots:
        tr = results[rpos] if rpos < len(results) else ''
        rpos += 1
        per_node[node].append({'i': token_index, 'lead': lead, 'core': core, 'tail': tail, 'tr': tr or ''})

    # Apply to each node by reconstructing as a sequence of NavigableString and inline spans
    for node, slot_list in per_node.items():
        try:
            tokens = _re2.split(r'(\r?\n)', str(node))
            map_idx = {s['i']: s for s in slot_list}
            pieces = []
            for idx, tok in enumerate(tokens):
                if tok in ('\n','\r\n'):
                    pieces.append(NavigableString(tok))
                    continue
                slot = map_idx.get(idx)
                if not slot:
                    pieces.append(NavigableString(tok))
                else:
                    # original + single space + colored translation + trailing
                    before = f"{slot['lead']}{slot['core']} "
                    pieces.append(NavigableString(before))
                    span = soup.new_tag('span')
                    span[MARK_ATTR] = 'inplace'
                    cls = set(span.get('class', []) or [])
                    cls.add(NOTRANSLATE_CLASS)
                    span['class'] = list(cls)
                    # vivid color, keep other typography unchanged
                    span['style'] = 'color:#16a34a;font:inherit;line-height:inherit;'
                    span.string = slot['tr']
                    pieces.append(span)
                    if slot['tail']:
                        pieces.append(NavigableString(slot['tail']))

            # replace original text node with new fragments
            for p in pieces:
                node.insert_before(p)
            node.extract()
        except Exception:
            continue

    return str(soup)
