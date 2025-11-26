"""Quick integration test: fetch one mail via IMAP, extract paper links with Gemini,
load each link via Playwright, and ask Gemini to read the page and return abstracts.

Usage:
    python scripts/test_abstract_pipeline.py [--folder FOLDER] [--uid UID]

The script only reads data (fetches message bodies with BODY.PEEK), does not modify
any mailbox state, and prints a JSON summary at the end.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import (  # type: ignore
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.config import load_config
from mailbot.imap_client import connect, fetch_raw, parse_message, pick_html_or_text
from mailbot.jobs import deepseek_summarize, new_openai
from mailbot.utils import decode_subject

logger = logging.getLogger("abstract_test")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


@dataclass(slots=True)
class GeminiClient:
    cli: Any
    model: str
    enable_thinking: bool
    thinking_budget: int
    timeout: float


def _clean_lines(text: str, limit: int | None = None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    joined = "\n".join(line for line in lines if line)
    if limit and len(joined) > limit:
        return joined[:limit]
    return joined


def _extract_json_block(raw: str) -> dict | list | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    candidates = [raw]
    lowered = raw.lower()
    if lowered.startswith("json"):
        trimmed = raw[4:].lstrip(":").lstrip()
        candidates.append(trimmed)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = raw[start : end + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None


def _html_to_text(html: str, limit: int = 20000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return _clean_lines(text, limit=limit)


def _get_email_text(msg) -> str:
    html, plain = pick_html_or_text(msg)
    if html:
        return _html_to_text(html, limit=20000)
    return _clean_lines(plain or "", limit=20000)


def _build_gemini_client(cfg: dict) -> GeminiClient:
    llm_cfg = cfg.get("llm", {})
    provider = llm_cfg.get("gemini") or cfg.get("gemini")
    if not provider:
        raise SystemExit("No Gemini configuration found (llm.gemini or gemini).")
    timeout = float(llm_cfg.get("request_timeout_seconds", 30.0))
    cli = new_openai(provider["api_base"], provider["api_key"], timeout=timeout)
    model = provider.get("model", "gemini-2.5-pro")
    enable_thinking = bool(llm_cfg.get("enable_thinking", True))
    thinking_budget = int(llm_cfg.get("thinking_budget", -1))
    return GeminiClient(
        cli=cli,
        model=model,
        enable_thinking=enable_thinking,
        thinking_budget=thinking_budget,
        timeout=timeout,
    )


def _call_gemini(
    g: GeminiClient,
    prompt: str,
    text: str,
    expect_json: bool = False,
) -> tuple[str, dict]:
    content, thinking, meta = deepseek_summarize(
        g.cli,
        g.model,
        prompt,
        text,
        enable_thinking=g.enable_thinking,
        thinking_budget=g.thinking_budget,
        timeout=g.timeout,
        expect_json=expect_json,
    )
    if thinking:
        meta = {**meta, "reasoning_preview": thinking[:800]}
    return content or "", meta


def extract_article_links(
    g: GeminiClient,
    email_text: str,
    max_items: int,
) -> list[dict]:
    prompt = textwrap.dedent(
        """
        You are a meticulous research email parser. Read the email content and
        list each distinct journal alert or paper mentioned. Return JSON with
        the schema: {
          "articles": [
            {
              "title": "short paper title (if known)",
              "journal": "journal/publisher/source",
              "url": "https://...",
              "notes": "1-2 keywords or issue info"
            }
          ]
        }.
        Only include entries that contain at least one HTTP/HTTPS link that
        points to the paper or its journal page. Never invent links.
        Keep at most {max_items} items and preserve the original link text when possible.
        """
    ).strip()
    payload = f"EMAIL CONTENT:\n{email_text}"[:10000]
    raw, meta = _call_gemini(g, prompt, payload, expect_json=True)
    data = _extract_json_block(raw)
    if not isinstance(data, dict):
        logger.warning("Gemini response for link extraction was not JSON; falling back to regex.")
        return _fallback_links(email_text, max_items)
    items = data.get("articles") if isinstance(data.get("articles"), list) else []
    cleaned: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        cleaned.append(
            {
                "title": (item.get("title") or "").strip(),
                "journal": (item.get("journal") or "").strip(),
                "url": url,
                "notes": (item.get("notes") or "").strip(),
            }
        )
        if len(cleaned) >= max_items:
            break
    if not cleaned:
        return _fallback_links(email_text, max_items)
    return cleaned


def _fallback_links(email_text: str, max_items: int) -> list[dict]:
    urls = []
    for match in _URL_RE.finditer(email_text):
        url = match.group(0).rstrip(".,)")
        if any(url == existing["url"] for existing in urls):
            continue
        urls.append({"title": "", "journal": "", "url": url, "notes": ""})
        if len(urls) >= max_items:
            break
    return urls


class PageFetcher:
    def __init__(self, headless: bool = True, extra_wait_ms: int = 1500, timeout_ms: int = 30000):
        self.headless = headless
        self.extra_wait_ms = extra_wait_ms
        self.timeout_ms = timeout_ms
        self._ctx = None
        self._browser = None
        self._page = None

    def __enter__(self):
        self._ctx = sync_playwright().start()
        self._browser = self._ctx.chromium.launch(headless=self.headless)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._ctx:
                self._ctx.stop()

    def fetch(self, url: str) -> dict:
        if not self._page:
            raise RuntimeError("PageFetcher not initialized")
        logger.info("Loading URL via Playwright: %s", url)
        page = self._page
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(self.extra_wait_ms)
        html = page.content()
        text = _html_to_text(html, limit=16000)
        title = page.title()
        final_url = page.url
        return {
            "url": url,
            "final_url": final_url,
            "title": title,
            "text": text,
            "html": html,
        }


def extract_abstract_from_page(g: GeminiClient, page_payload: dict) -> dict:
    text = page_payload.get("text") or ""
    meta = {
        "final_url": page_payload.get("final_url"),
        "title": page_payload.get("title"),
    }
    prompt = textwrap.dedent(
        """
        You are parsing a scholarly article web page. Identify whether an abstract
        is present and return JSON with this schema:
        {
          "has_abstract": true|false,
          "abstract": "verbatim abstract text",
          "language": "en" or "zh" or other ISO code,
          "confidence": 0.0-1.0,
          "source_hint": "where the text was found (e.g., meta tag, Abstract section)"
        }.
        Only quote text you can see in the provided content. If no abstract exists
        or the page is a teaser, set has_abstract=false and keep abstract="".
        Prefer the English abstract if multiple languages exist.
        """
    ).strip()
    body = f"URL: {meta['final_url']}\nTitle: {meta['title']}\n\nPAGE TEXT:\n{text}"[:14000]
    raw, _ = _call_gemini(g, prompt, body, expect_json=True)
    data = _extract_json_block(raw)
    if not isinstance(data, dict):
        return {
            "has_abstract": False,
            "abstract": "",
            "language": "",
            "confidence": 0.0,
            "source_hint": "Gemini did not return JSON",
        }
    return {
        "has_abstract": bool(data.get("has_abstract", bool(data.get("abstract")))),
        "abstract": (data.get("abstract") or "").strip(),
        "language": (data.get("language") or "").strip(),
        "confidence": float(data.get("confidence", 0.0) or 0.0),
        "source_hint": (data.get("source_hint") or "").strip(),
    }


def fetch_sample_email(cfg: dict, folder: str, uid: Optional[int]) -> tuple[int, str, str]:
    imap_cfg = cfg["imap"]
    client = connect(
        imap_cfg["server"],
        imap_cfg["email"],
        imap_cfg["password"],
        port=imap_cfg.get("port", 993),
        ssl=imap_cfg.get("ssl", True),
    )
    try:
        client.select_folder(folder)
        if uid is None:
            uids = client.search(["ALL"])
            if not uids:
                raise SystemExit(f"Folder {folder} has no messages.")
            uid = uids[-1]
        raw = fetch_raw(client, uid)
        msg = parse_message(raw)
        subject = decode_subject(msg)
        logger.info("Fetched UID %s from %s | Subject: %s", uid, folder, subject)
        email_text = _get_email_text(msg)
        return uid, subject, email_text
    finally:
        try:
            client.logout()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Test pipeline: IMAP -> Gemini -> Playwright -> Gemini")
    parser.add_argument("--folder", default=None, help="IMAP folder to sample from (default: config.imap.folder or INBOX)")
    parser.add_argument("--uid", type=int, default=None, help="Specific UID to fetch (default: newest)")
    parser.add_argument("--max-articles", type=int, default=4, help="Max number of article links to follow")
    parser.add_argument("--headless", dest="headless", action="store_true", help="Run Playwright headless (default)")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Disable headless browsing for debugging")
    parser.set_defaults(headless=True)
    args = parser.parse_args()

    cfg = load_config()
    folder = args.folder or cfg.get("imap", {}).get("folder") or "INBOX"

    uid, subject, email_text = fetch_sample_email(cfg, folder, args.uid)
    if not email_text:
        raise SystemExit("Email body is empty; nothing to parse.")

    gem = _build_gemini_client(cfg)
    articles = extract_article_links(gem, email_text, max_items=args.max_articles)
    if not articles:
        raise SystemExit("No article links found in this email.")

    logger.info("Identified %d candidate article links", len(articles))

    results: list[dict] = []
    with PageFetcher(headless=args.headless) as fetcher:
        for idx, item in enumerate(articles, start=1):
            url = item["url"]
            try:
                page_payload = fetcher.fetch(url)
            except PlaywrightTimeoutError as exc:
                logger.warning("Playwright timed out for %s: %s", url, exc)
                results.append({**item, "error": f"timeout: {exc}"})
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Playwright failed for %s: %s", url, exc)
                results.append({**item, "error": str(exc)})
                continue

            abstract_info = extract_abstract_from_page(gem, page_payload)
            results.append(
                {
                    **item,
                    **abstract_info,
                    "page_title": page_payload.get("title"),
                    "final_url": page_payload.get("final_url"),
                }
            )

    print(
        json.dumps(
            {
                "folder": folder,
                "uid": uid,
                "subject": subject,
                "articles": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
