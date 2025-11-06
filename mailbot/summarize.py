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
from .jobs import _save_summary_payload, new_openai, deepseek_summarize
from .utils import rough_token_count

logger = logging.getLogger("mailbot")


def summarize_once(cfg: dict, folder: str | None = None, batch: int = 5):
    imap = cfg["imap"]
    folder = folder or imap.get("folder", "INBOX")
    exclude = [cfg.get("prefix", {}).get("translate", "[机器翻译]"), cfg.get("prefix", {}).get("summarize", "[机器总结]")]
    client = connect(imap["server"], imap["email"], imap["password"], port=imap.get("port", 993), ssl=True)

    try:
        uids = search_unseen_without_prefix(client, folder, exclude_prefixes=exclude, exclude_auto_generated=True, robust=True, fetch_chunk=int(cfg.get('summarize', {}).get('unseen_fetch_chunk', 500)))
        logger.info(f"Summarize once: scanning folder {folder}, UNSEEN={len(uids)} (robust, auto-generated excluded)")
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

        llm_cfg = cfg.get("llm", {})
        timeout = float(llm_cfg.get("summarize_timeout_seconds", llm_cfg.get("request_timeout_seconds", 15.0)))
        provider = llm_cfg.get("siliconflow") or cfg.get("siliconflow") or cfg.get("siliconflow2")
        if not provider:
            raise ValueError("No LLM provider configured. Set llm.siliconflow or siliconflow in config.json")
        model = llm_cfg.get("summarizer_model") or provider.get("model") or "deepseek-ai/DeepSeek-V3.2-Exp"
        enable_thinking = bool(llm_cfg.get("enable_thinking", True))
        thinking_budget = int(llm_cfg.get("thinking_budget", 4096))
        use_mock = bool(llm_cfg.get("mock", False) or cfg.get("test", {}).get("mock_llm", False))
        prompt_path = Path(llm_cfg.get('prompt_file', 'Prompt.txt'))
        prompt = prompt_path.read_text(encoding='utf-8') if prompt_path.exists() else 'Summarize in Chinese.'
        cli = new_openai(provider["api_base"], provider["api_key"], timeout=timeout) if not use_mock else None

        # create a run file early with meta
        run_start = datetime.now()
        run_ts = run_start.strftime('%Y%m%d-%H%M%S')
        run_path = Path(__file__).resolve().parents[1] / 'data' / f'summarize-{run_ts}.json'
        meta = {
            "mode": "once",
            "folder": folder,
            "batch": int(batch),
            "model": model,
            "enable_thinking": bool(enable_thinking),
            "mock": bool(use_mock),
            "start_time": run_start.isoformat(timespec='seconds'),
            "run_id": run_ts,
            "entries_written": 0,
        }
        _save_summary_payload([], path=run_path, meta=meta)

        if not filtered:
            meta["end_time"] = datetime.now().isoformat(timespec='seconds')
            _save_summary_payload([], path=run_path, meta=meta)
            return None

        items = []
        submitted_entries: list[dict] = []
        for idx_pair, (uid, msg) in enumerate(filtered, start=1):
            sub = str(msg.get("Subject", ""))
            logger.info(f"Processing subject (summarize once): {sub} (uid={uid})")
            html, text = pick_html_or_text(msg)
            plain = text or (html or "")
            if not plain:
                mark_seen(client, folder, uid)
                continue
            total_chars = len(plain)
            total_tokens = rough_token_count(plain)
            snippet = plain[:4000]
            sn_chars = len(snippet)
            sn_tokens = rough_token_count(snippet)
            logger.info(f"Summarize-once plan: total chars={total_chars}, ~tokens={total_tokens} → snippet chars={sn_chars}, ~tokens={sn_tokens}")
            # call model and record outputs
            if use_mock:
                summ, thinking = LLMClient(provider["api_base"], provider["api_key"], model, timeout=timeout).summarize(snippet, lang="zh-CN"), ''
                parsed = None
            else:
                summ, thinking = deepseek_summarize(cli, model, prompt, snippet, enable_thinking, thinking_budget, timeout=timeout, expect_json=True)
                try:
                    import json as _json
                    parsed = _json.loads(summ)
                except Exception:
                    parsed = None
            # record single-chunk payload for summarize_once
            submitted_entries.append({
                "job": "summarize_once",
                "folder": folder,
                "uid": uid,
                "subject": sub,
                "chunk_index": 1,
                "chunk_total": 1,
                "text": snippet,
                "chars": sn_chars,
                "approx_tokens": sn_tokens,
                "prompt": prompt,
                "model": model,
                "enable_thinking": bool(enable_thinking),
                "thinking_budget": int(thinking_budget),
                "thinking": thinking,
                "answer": summ,
                "when": datetime.now().isoformat(timespec='seconds'),
                "mock": bool(use_mock),
            })
            # prefer JSON → render cards; else keep raw summary
            if parsed and isinstance(parsed.get("articles"), list):
                cards = []
                for a in parsed["articles"][:12]:
                    if not isinstance(a, dict):
                        continue
                    tzh = (a.get('title_zh') or '').strip()
                    ten = (a.get('title_en') or '').strip()
                    authors = (a.get('authors') or '').strip()
                    bullets = [b for b in (a.get('bullets') or []) if (b or '').strip()]
                    rel = (a.get('relevance') or '').strip()
                    lis = ''.join(f"<li>{b}</li>" for b in bullets[:3])
                    card = f"<div style=\"border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;margin:10px 0;\"><div style=\"font-weight:700;font-size:15px;line-height:1.35;margin-bottom:6px;\"><span style=\"color:#111827;\">中文标题：</span><span style=\"color:#111827;\">{tzh}</span></div><div style=\"font-size:12px;color:#374151;margin-bottom:4px;\">English Title: {ten}</div><div style=\"font-size:12px;color:#6b7280;margin-bottom:6px;\">Authors: {authors}</div><div><div style=\"font-weight:600;color:#111827;margin-bottom:4px;\">要点</div><ul style=\"margin:0;padding-left:18px;\">{lis}</ul><div style=\"font-size:12px;color:#059669;margin-top:6px;\">相关性：{rel}</div></div></div>"
                cards.append(card)
                rendered = ''.join(cards)
                if not rendered:
                    rendered = "<div style=\"color:#888;\">没有相关内容</div>"
                items.append((msg, rendered))
            else:
                items.append((msg, summ))
            # checkpoint after each item
            meta["entries_written"] = len(submitted_entries)
            meta["last_update"] = datetime.now().isoformat(timespec='seconds')
            _save_summary_payload(submitted_entries, path=run_path, meta=meta)
        if not items:
            meta["end_time"] = datetime.now().isoformat(timespec='seconds')
            _save_summary_payload(submitted_entries, path=run_path, meta=meta)
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
        def _looks_like_html(s: str) -> bool:
            if not s:
                return False
            s = s.strip()
            return s.startswith('<') and ('</' in s or '/>' in s)

        cards = []
        for m, summ in items:
            subj = str(m.get('Subject','') or '')
            body = (summ if _looks_like_html(summ) else _bullets(summ)) if summ else "<div style=\"color:#888;\">(empty)</div>"
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
        # persist submitted payloads for this run
        meta["end_time"] = datetime.now().isoformat(timespec='seconds')
        _save_summary_payload(submitted_entries, path=run_path, meta=meta)
        return len(items)
    finally:
        try:
            client.logout()
        except Exception:
            pass
