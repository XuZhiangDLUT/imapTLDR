from .llm import LLMClient
from .imap_client import (
    connect,
    search_unseen_without_prefix,
    fetch_raw,
    parse_message,
    build_email,
    append_unseen,
    mark_seen,
    pick_html_or_text,
)
import logging

logger = logging.getLogger("mailbot")


def summarize_once(cfg: dict, folder: str | None = None, batch: int = 5):
    imap = cfg["imap"]
    folder = folder or imap.get("folder", "INBOX")
    exclude = [cfg.get("prefix", {}).get("translate", "[机器翻译]"), cfg.get("prefix", {}).get("summarize", "[机器总结]")]
    client = connect(imap["server"], imap["email"], imap["password"], port=imap.get("port", 993), ssl=True)

    try:
        uids = search_unseen_without_prefix(client, folder, exclude_prefixes=exclude)
        logger.info(f"Summarize once: scanning folder {folder}")
        # client-side filter to avoid non-ASCII SEARCH
        filtered = []
        for uid in uids:
            raw = fetch_raw(client, uid)
            msg = parse_message(raw)
            sub = str(msg.get("Subject", ""))
            if any(p in sub for p in exclude):
                continue
            logger.info(f"Detected subject (summarize once): {sub} (uid={uid})")
            filtered.append((uid, msg))
            if len(filtered) >= batch:
                break
        if not filtered:
            return None

        llm_cfg = cfg.get("llm", {})
        timeout = float(llm_cfg.get("summarize_timeout_seconds", llm_cfg.get("request_timeout_seconds", 15.0)))
        provider = llm_cfg.get("siliconflow") or cfg.get("siliconflow") or cfg.get("siliconflow2")
        if not provider:
            raise ValueError("No LLM provider configured. Set llm.siliconflow or siliconflow in config.json")
        model = llm_cfg.get("summarizer_model") or provider.get("model") or "deepseek-ai/DeepSeek-V3.2-Exp"
        llm = LLMClient(provider["api_base"], provider["api_key"], model, timeout=timeout)  # deepseek for summarization

        parts = []
        for uid, msg in filtered:
            sub = str(msg.get("Subject", ""))
            logger.info(f"Processing subject (summarize once): {sub} (uid={uid})")
            html, text = pick_html_or_text(msg)
            plain = text or (html or "")
            if not plain:
                mark_seen(client, folder, uid)
                continue
            snippet = plain[:4000]
            summ = llm.summarize(snippet, lang="zh-CN")
            parts.append(f"- {sub}\n{summ}\n")
        if not parts:
            return None

        body = "\n\n".join(parts)
        out = build_email(
            subject=f"[机器总结] {len(parts)} 封邮件汇总",
            from_addr=imap["email"],
            to_addr=imap["email"],
            html=None,
            text=body,
        )
        append_unseen(client, folder, out)
        logger.info(f"Appended summary (once): [机器总结] {len(parts)} 封邮件汇总")
        for uid in uids:
            mark_seen(client, folder, uid)
        return len(parts)
    finally:
        try:
            client.logout()
        except Exception:
            pass
