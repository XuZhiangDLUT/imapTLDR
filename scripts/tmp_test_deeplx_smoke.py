from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.config import load_config
from mailbot.jobs import deeplx_translate_single


def main() -> int:
    parser = argparse.ArgumentParser(description="Temporary DeepLX smoke test.")
    parser.add_argument("--text", default="Hello DeepLX, this is a smoke test.")
    parser.add_argument("--source-lang", default="auto")
    parser.add_argument("--target-lang", default="ZH")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    cfg = load_config()
    llm = cfg.get("llm", {}) or {}
    providers = llm.get("linuxdo", {}) or llm.get("providers", {}) or {}
    deeplx = providers.get("deeplx", {}) or {}
    api_base = str(deeplx.get("api_base") or "").strip()
    api_key = str(deeplx.get("api_key") or "").strip()

    if not api_base:
        print("DeepLX is not configured: llm.linuxdo.deeplx.api_base is empty.")
        return 2

    out = deeplx_translate_single(
        api_base,
        api_key,
        args.text,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        timeout=args.timeout,
    )
    cjk = len(re.findall(r"[\u4e00-\u9fff]", out or ""))

    print(f"endpoint={api_base}")
    print(f"input={args.text}")
    print(f"output={out}")
    print(f"output_len={len(out or '')}, cjk_count={cjk}")

    if out:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
