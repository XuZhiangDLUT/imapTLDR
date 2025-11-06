from __future__ import annotations
from pathlib import Path
from typing import Generator, Iterable
from openai import OpenAI
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger("mailbot")
from .imap_client import (
    connect,
    list_unseen,
    fetch_raw,
    parse_message,
    pick_html_or_text,
    build_email,
    append_unseen,
    mark_seen,
    has_linked_reply,
)
from .immersion import inject_bilingual_html
from .utils import decode_subject, pass_prefix, split_by_chars
from .mock_llm import translate_batch_mock, summarize_mock


# --- scanning helpers ---

DEFAULT_TRANSLATE_FOLDERS = [
    "IJSS","TWS","JMPS","EML","PRL","IJMS","IJNME","CMAME","ComputerStruct","SMO",
    "ES","NLDyna","JSV","IJIE","OceanEng","Def. Technol.","Eur.J.Mech.","CompositeStruct",
]
DEFAULT_SUMMARY_FOLDERS = [
    "其他文件夹/Nature","其他文件夹/APS Extended","其他文件夹/PNAS","其他文件夹/Science",
    "其他文件夹/Materials","其他文件夹/AFM","其他文件夹/AdvMaterial","其他文件夹/R. Soc. A","其他文件夹/Adv.Sci.",
]


def scan_translate_targets(c, cfg: dict, excluded_prefixes: Iterable[str]) -> Generator[tuple[str,int,object], None, None]:
    imap = cfg['imap']
    translate_cfg = cfg.get('translate', {})
    folders = translate_cfg.get('folders', DEFAULT_TRANSLATE_FOLDERS)
    max_per = int(translate_cfg.get('max_per_run_per_folder', 3))

    # normal folders
    for folder in folders:
        target_folder = folder if folder.startswith('INBOX') or folder.startswith('其他文件夹/') else f"其他文件夹/{folder}"
        logger.info(f"Scanning folder: {target_folder}")
        uids = list_unseen(c, target_folder)
        count = 0
        for uid in uids:
            raw = fetch_raw(c, uid)
            msg = parse_message(raw)
            sub = decode_subject(msg)
            if not pass_prefix(sub, excluded_prefixes):
                continue
            logger.info(f"Detected subject (translate): {sub} (uid={uid})")
            yield (target_folder, uid, msg)
            count += 1
            if count >= max_per:
                break

    # INBOX keyword channel
    inbox_keywords = translate_cfg.get('inbox_keywords', ["相关研究汇总","快讯汇总"])
    inbox_froms = translate_cfg.get('inbox_from', ["scholaralerts-noreply@google.com"]) 
    uids = list_unseen(c, 'INBOX')
    logger.info("Scanning folder: INBOX (keyword channel)")
    for uid in uids:
        raw = fetch_raw(c, uid)
        msg = parse_message(raw)
        sub = decode_subject(msg)
        if not pass_prefix(sub, excluded_prefixes):
            continue
        sender = str(msg.get('From', ''))
        if any(k in sub for k in inbox_keywords) or any(f in sender for f in inbox_froms):
            logger.info(f"Detected subject (translate INBOX): {sub} (from={sender}, uid={uid})")
            yield ('INBOX', uid, msg)


# --- LLM helpers ---

def new_openai(base_url: str, api_key: str, timeout: float | int = 15.0) -> OpenAI:
    base = base_url.rstrip('/')
    if not base.endswith('/v1'):
        base += '/v1'
    # configurable timeout (default 15s)
    logger.info(f"Initialize LLM client base={base}")
    return OpenAI(base_url=base, api_key=api_key, timeout=timeout)


def qwen_translate_batch(cli: OpenAI, model: str, segments: list[str], timeout: float | int = 15.0) -> list[str]:
    if not segments:
        return []
    sys = "严格逐段翻译为中文。保持数字、专有名词与标点。不要添加解释。"
    user = "\n\n-----\n\n".join(segments)
    try:
        r = cli.chat.completions.create(
            model=model, temperature=0.2,
            messages=[{"role":"system","content":sys},{"role":"user","content":user}],
            timeout=timeout,
        )
        out = (r.choices[0].message.content or '').split("\n\n-----\n\n")
    except Exception as e:
        logger.info(f"LLM translate error or timeout: {e}")
        # fallback: return empty translations to avoid blocking tests
        out = [''] * len(segments)
    if len(out) < len(segments):
        out += ['']*(len(segments)-len(out))
    return [s.strip() for s in out][:len(segments)]


def deepseek_summarize(cli: OpenAI, model: str, prompt: str, text: str, enable_thinking: bool, thinking_budget: int, timeout: float | int = 15.0) -> str:
    extra = {"enable_thinking": enable_thinking, "thinking_budget": thinking_budget} if enable_thinking else {}
    try:
        r = cli.chat.completions.create(
            model=model, temperature=0.2,
            messages=[{"role":"system","content":prompt},{"role":"user","content":text}],
            extra_body=extra or None,
            timeout=timeout,
        )
        return r.choices[0].message.content or ''
    except Exception as e:
        logger.info(f"LLM summarize error or timeout: {e}")
        return '(summary timeout or error)'


# --- Jobs ---

def translate_job(cfg: dict):
    logger.info("Translate job started")
    imap = cfg['imap']; pref = cfg.get('prefix', {'translate':'[机器翻译]','summarize':'[机器总结]'})
    excluded = [pref.get('translate','[机器翻译]'), pref.get('summarize','[机器总结]')]
    sf = cfg.get('llm', {}).get('siliconflow') or cfg.get('siliconflow2') or cfg.get('siliconflow')
    use_mock = bool(cfg.get('llm', {}).get('mock', False) or cfg.get('test', {}).get('mock_llm', False))
    llm_cfg = cfg.get('llm', {})
    translate_timeout = float(llm_cfg.get('translate_timeout_seconds', llm_cfg.get('request_timeout_seconds', 15.0)))
    if not use_mock:
        if sf is None:
            # backwards compat
            sf = cfg.get('siliconflow2') or cfg.get('siliconflow')
            cli = new_openai(sf['api_base'], sf['api_key'], timeout=translate_timeout)
            trans_model = cfg.get('llm', {}).get('translator_model') or sf.get('model')
        else:
            cli = new_openai(sf['api_base'], sf['api_key'], timeout=translate_timeout)
            trans_model = cfg.get('llm', {}).get('translator_model') or cfg.get('siliconflow2',{}).get('model') or 'Qwen/Qwen2.5-7B-Instruct'

    c = connect(imap['server'], imap['email'], imap['password'], port=imap.get('port',993), ssl=imap.get('ssl',True))
    try:
        for folder, uid, msg in scan_translate_targets(c, cfg, excluded):
            sub = decode_subject(msg)
            logger.info(f"Processing subject (translate): {sub} in {folder} (uid={uid})")
            html, text = pick_html_or_text(msg)
            if not html and text:
                html = f"<html><body><pre>{text}</pre></body></html>"
            if not html:
                logger.info("Skip empty body; mark seen")
                mark_seen(c, folder, uid)
                continue

            # idempotency: skip if already handled
            orig_msgid = msg.get('Message-ID') or ''
            if orig_msgid and has_linked_reply(c, folder, orig_msgid, pref.get('translate','[机器翻译]')):
                logger.info("Skip already translated (idempotent)")
                mark_seen(c, folder, uid)
                continue

            if use_mock:
                zh_html = inject_bilingual_html(html, translate_batch_mock)
            else:
                zh_html = inject_bilingual_html(html, lambda segs: qwen_translate_batch(cli, trans_model, segs, timeout=translate_timeout))
            new_subject = f"{pref.get('translate','[机器翻译]')} {sub}"
            out = build_email(new_subject, imap['email'], imap['email'], zh_html, None, in_reply_to=msg.get('Message-ID'))
            append_unseen(c, folder or 'INBOX', out)
            mark_seen(c, folder, uid)
            logger.info(f"Appended translated mail: {new_subject}")
    finally:
        try:
            c.logout()
        except Exception:
            pass
    logger.info("Translate job finished")


def summarize_job(cfg: dict):
    logger.info("Summarize job started")
    imap = cfg['imap']; pref = cfg.get('prefix', {'translate':'[机器翻译]','summarize':'[机器总结]'})
    excluded = [pref.get('translate','[机器翻译]'), pref.get('summarize','[机器总结]')]

    llm_cfg = cfg.get('llm', {})
    use_mock = bool(llm_cfg.get('mock', False) or cfg.get('test', {}).get('mock_llm', False))
    if not use_mock:
        sf = llm_cfg.get('siliconflow') or cfg.get('siliconflow')
        summarize_timeout = float(llm_cfg.get('summarize_timeout_seconds', llm_cfg.get('request_timeout_seconds', 15.0)))
        cli = new_openai(sf['api_base'], sf['api_key'], timeout=summarize_timeout)
        model = llm_cfg.get('summarizer_model') or sf.get('model')
        enable_thinking = llm_cfg.get('enable_thinking', True)
        thinking_budget = int(llm_cfg.get('thinking_budget', 4096))
    else:
        cli = None
        model = ''
        enable_thinking = False
        thinking_budget = 0
    prompt_path = Path(llm_cfg.get('prompt_file', 'Prompt.txt'))
    prompt = prompt_path.read_text(encoding='utf-8') if prompt_path.exists() else 'Summarize in Chinese.'

    folders = cfg.get('summarize', {}).get('folders', DEFAULT_SUMMARY_FOLDERS)
    batch_size = int(cfg.get('summarize', {}).get('batch_size', 10))
    chunk_chars = int(cfg.get('summarize', {}).get('chunk_tokens', 16000))  # approx by chars

    c = connect(imap['server'], imap['email'], imap['password'], port=imap.get('port',993), ssl=imap.get('ssl',True))
    try:
        for folder in folders:
            logger.info(f"Scanning folder (summarize): {folder}")
            uids = list_unseen(c, folder)
            pairs = []
            for uid in uids:
                raw = fetch_raw(c, uid)
                msg = parse_message(raw)
                sub = decode_subject(msg)
                if not pass_prefix(sub, excluded):
                    continue
                logger.info(f"Detected subject (summarize): {sub} (uid={uid})")
                html, txt = pick_html_or_text(msg)
                plain = BeautifulSoup(html, 'html5lib').get_text('\n', strip=True) if html else (txt or '')
                if not plain:
                    mark_seen(c, folder, uid)
                    continue
                chunks = split_by_chars(plain, chunk_chars)
                answers = []
                for ch in chunks:
                    if use_mock:
                        answers.append(summarize_mock(ch))
                    else:
                        answers.append(deepseek_summarize(cli, model, prompt, ch, enable_thinking, thinking_budget, timeout=summarize_timeout))
                pairs.append((uid, msg, '\n\n'.join(answers)))

            for i in range(0, len(pairs), batch_size):
                batch = pairs[i:i+batch_size]
                if not batch:
                    continue
                html = "<html><body><ol>" + "".join(
                    f"<li><b>{decode_subject(m)}</b><br/>{summ}</li>" for _, m, summ in batch
                ) + "</ol></body></html>"
                subject = f"{pref.get('summarize','[机器总结]')} {folder}（{len(batch)}封）"
                out = build_email(subject, imap['email'], imap['email'], html, None)
                append_unseen(c, folder, out)
                logger.info(f"Appended summary: {subject}")
                for uid, _, _ in batch:
                    mark_seen(c, folder, uid)
    finally:
        try:
            c.logout()
        except Exception:
            pass
    logger.info("Summarize job finished")
