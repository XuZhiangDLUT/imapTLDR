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
from datetime import datetime
from premailer import transform as inline_css

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

        items = []
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
            items.append((msg, summ))
        if not items:
            return None

        def _bullets(text: str) -> str:
            lines = [l.strip() for l in (text or '').splitlines()]
            lis = []
            for l in lines:
                if not l:
                    continue
                if l.startswith(('- ', '• ', '* ')):
                    l = l[2:].strip()
                lis.append(f"<li>{l}</li>")
            return "<ul style=\"margin:0; padding-left:18px;\">" + "".join(lis) + "</ul>"

        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        cards = []
        for m, summ in items:
            subj = str(m.get('Subject','') or '')
            body = _bullets(summ) if summ else "<div style=\"color:#888;\">(empty)</div>"
            cards.append(
                f"""
                <li style=\"margin-bottom:14px;\">
                  <div style=\"font-weight:600; margin-bottom:6px;\">{subj}</div>
                  {body}
                </li>
                """
            )
        body_html = f"""
        <html>
          <body>
            <div style=\"max-width:760px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1f2937;\">
              <div style=\"background:#f3f4f6;border:1px solid #e5e7eb;border-radius:8px;padding:12px 16px;margin:12px 0;\">
                <div style=\"font-size:16px;font-weight:600;\">机器总结 · {folder}</div>
                <div style=\"font-size:12px;color:#6b7280;\">生成时间：{now}</div>
              </div>
              <ol style=\"margin:0;padding-left:20px;\">{''.join(cards)}</ol>
              <div style=\"margin-top:12px;font-size:12px;color:#9ca3af;\">自动生成 · 如有误请忽略</div>
            </div>
          </body>
        </html>
        """
        body_html = inline_css(body_html)
        out = build_email(
            subject=f"[机器总结] {len(items)} 封邮件汇总",
            from_addr=imap["email"],
            to_addr=imap["email"],
            html=body_html,
            text=None,
        )
        append_unseen(client, folder, out)
        logger.info(f"Appended summary (once): [机器总结] {len(items)} 封邮件汇总")
        for uid in uids:
            mark_seen(client, folder, uid)
        return len(items)
    finally:
        try:
            client.logout()
        except Exception:
            pass
