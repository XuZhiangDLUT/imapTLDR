from bs4 import BeautifulSoup
from premailer import transform as inline_css
from .llm import LLMClient
from .imap_client import (
    connect,
    search_unseen_without_prefix,
    fetch_raw,
    parse_message,
    pick_html_or_text,
    build_email,
    append_unseen,
    mark_seen,
)
import logging

logger = logging.getLogger("mailbot")


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


def _should_inject(tag) -> bool:
    if tag.find_parent("blockquote"):
        return False
    # avoid header/footer/nav/legal blocks
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


def extract_segments_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html5lib")
    # simple paragraph segmentation; skip empty
    segs = []
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3"]):
        text = tag.get_text(" ", strip=True)
        if text:
            segs.append(text)
    # fallback to full text
    if not segs:
        text = soup.get_text(" ", strip=True)
        if text:
            segs = [text]
    return segs


def render_bilingual_html(original_html: str, translations: list[str]) -> str:
    soup = BeautifulSoup(original_html, "html5lib")
    idx = 0
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3"]):
        if not _should_inject(tag):
            continue
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if idx >= len(translations):
            break
        tr = translations[idx]
        idx += 1
        # insert translated line after the original
        ins = soup.new_tag("div")
        ins.string = tr
        ins["style"] = (
            "color:#0B6; margin-top:4px; display:block; line-height:1.45; font-size:0.95em;"
            "word-break:break-word; white-space:normal;"
        )
        tag.insert_after(ins)
    # inline CSS for email client compatibility
    return inline_css(str(soup))


def translate_once(cfg: dict, max_items: int = 2):
    imap = cfg["imap"]
    exclude = [cfg.get("prefix", {}).get("translate", "[机器翻译]"), cfg.get("prefix", {}).get("summarize", "[机器总结]")]
    client = connect(imap["server"], imap["email"], imap["password"], port=imap.get("port", 993), ssl=True)

    try:
        folder = imap.get("folder", "INBOX")
        logger.info(f"Translate once: scanning folder {folder}")
        uids = search_unseen_without_prefix(client, folder, exclude_prefixes=exclude)
        # filter out subjects with excluded prefixes client-side
        filtered_uids = []
        for uid in uids:
            raw = fetch_raw(client, uid)
            msg = parse_message(raw)
            sub = str(msg.get("Subject", ""))
            if any(p in sub for p in exclude):
                continue
            logger.info(f"Detected subject (translate once): {sub} (uid={uid})")
            filtered_uids.append((uid, msg))
            if len(filtered_uids) >= max_items:
                break

        if not filtered_uids:
            return []

        # LLM init (translator) with configurable timeout and provider fallback
        llm_cfg = cfg.get("llm", {})
        timeout = float(llm_cfg.get("translate_timeout_seconds", llm_cfg.get("request_timeout_seconds", 15.0)))
        provider = llm_cfg.get("siliconflow") or cfg.get("siliconflow2") or cfg.get("siliconflow")
        if not provider:
            raise ValueError("No LLM provider configured. Set llm.siliconflow or siliconflow/siliconflow2 in config.json")
        model = (
            llm_cfg.get("translator_model")
            or (cfg.get("siliconflow2", {}) or {}).get("model")
            or (llm_cfg.get("siliconflow", {}) or {}).get("model")
            or "Qwen/Qwen2.5-7B-Instruct"
        )
        llm = LLMClient(provider["api_base"], provider["api_key"], model, timeout=timeout)

        results = []
        for uid, msg in filtered_uids:
            sub = str(msg.get("Subject", ""))
            logger.info(f"Processing subject (translate once): {sub} (uid={uid})")
            from_addr = str(msg.get("From", imap["email"]))
            to_addr = str(msg.get("To", imap["email"]))
            html, text = pick_html_or_text(msg)
            source = html or text or ""
            if not source.strip():
                logger.info("Skip empty body; mark seen")
                mark_seen(client, imap.get("folder", "INBOX"), uid)
                continue

            segs = extract_segments_from_html(html) if html else [text]
            trans = llm.translate_batch(segs, source_lang="auto", target_lang="zh-CN")
            if html:
                body_html = render_bilingual_html(html, trans)
                body_text = None
            else:
                body_html = None
                body_text = "\n\n".join([segs[0], "", "【译文】", "\n".join(trans)])

            new_subject = f"{exclude[0]} {sub}"  # [机器翻译]
            out = build_email(new_subject, from_addr, to_addr, body_html, body_text)
            append_unseen(client, imap.get("folder", "INBOX"), out)
            mark_seen(client, imap.get("folder", "INBOX"), uid)
            logger.info(f"Appended translated mail: {new_subject}")
            results.append((uid, new_subject))
        logger.info(f"Translate once finished: {len(results)} items processed")
        return results
    finally:
        try:
            client.logout()
        except Exception:
            pass
