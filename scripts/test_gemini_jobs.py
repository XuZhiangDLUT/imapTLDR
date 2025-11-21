from __future__ import annotations

"""Sanity-check calling Gemini 2.5 Pro via mailbot.jobs.deepseek_summarize.

This uses the llm.gemini section from config.json and does not modify any state.
"""

import json
from pathlib import Path
import sys

# Ensure project root is on sys.path so we can import mailbot.*
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mailbot.jobs import new_openai, deepseek_summarize  # type: ignore


def main() -> None:
    cfg_path = Path("config.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    llm = cfg.get("llm", {})
    gem = llm.get("gemini") or cfg.get("gemini")
    if not gem:
        raise SystemExit("No llm.gemini config found in config.json")

    api_base = gem["api_base"]
    api_key = gem["api_key"]
    model = gem.get("model", "gemini-2.5-pro")

    cli = new_openai(api_base, api_key, timeout=30.0)

    prompt = "You are a helpful assistant. Answer in concise Chinese bullet points."
    text = "请用 3 条要点总结一下牛顿三大定律，各用一句话说明。"

    content, thinking, meta = deepseek_summarize(
        cli,
        model,
        prompt,
        text,
        enable_thinking=True,
        thinking_budget=-1,
        timeout=30.0,
        expect_json=False,
    )

    print("[MODEL]", model)
    print("[ANSWER]\n", (content or "").strip())
    if thinking:
        print("\n[THINKING] (truncated)\n", str(thinking)[:800])
    if meta:
        usage = meta.get("usage") or {}
        if usage:
            print("\n[USAGE]", {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")})
        if "reasoning_tokens" in meta:
            print("[REASONING TOKENS]", meta.get("reasoning_tokens"))


if __name__ == "__main__":
    main()
