from __future__ import annotations

"""Quick test script for Gemini 2.5 Pro via x666 OpenAI-compatible endpoint.

This script is only for manual testing and won't be imported by the main app.
"""

from openai import OpenAI


BASE_URL = "https://x666.me/v1"  # OpenAI-compatible base URL for OpenAI-style API
API_KEY = "sk-XKmR94WbOxg0FZwEHcuUihTTgN8h8yohGt3r75RjnxHKuixu"
MODEL = "gemini-2.5-pro"


def main() -> None:
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=30.0)

    system = "You are a helpful assistant. Answer in concise Chinese."
    user = "请用 3 条要点总结一下牛顿三大定律，各用一句话说明。"

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            # Gemini thinking config (dynamic thinking: thinkingBudget = -1)
            extra_body={
                "generationConfig": {
                    "thinkingConfig": {
                        "thinkingBudget": -1,
                    }
                }
            },
        )
    except Exception as e:  # pragma: no cover - manual debugging helper
        print("[ERROR] Request failed:", repr(e))
        return

    # Inspect raw response type/shape first
    print("[RAW TYPE]", type(resp))
    print("[RAW]", repr(resp)[:800])

    # Normalize to a common structure
    msg = None
    if hasattr(resp, "choices"):
        # Standard OpenAI-style client object
        try:
            choice = resp.choices[0]
            msg = choice.message
        except Exception as e:  # pragma: no cover
            print("[ERROR] Failed to read choices from response:", repr(e))
            return
    elif isinstance(resp, str):
        # Some providers return raw JSON string
        try:
            import json as _json

            data = _json.loads(resp)
        except Exception as e:  # pragma: no cover
            print("[ERROR] Response is non-JSON string:", repr(e))
            return
        choice0 = (data.get("choices") or [{}])[0]
        msg = choice0.get("message") or {}
    elif isinstance(resp, dict):
        choice0 = (resp.get("choices") or [{}])[0]
        msg = choice0.get("message") or {}
    else:
        print("[ERROR] Unexpected response type; aborting.")
        return

    # Extract text content
    if isinstance(msg, dict):
        content = (msg.get("content") or "").strip()
    else:
        content = (getattr(msg, "content", None) or "").strip()

    print("[MODEL]", MODEL)
    print("[ANSWER]\n", content)

    # Best-effort extraction of Gemini thinking / reasoning fields (if any)
    thinking = ""
    try:
        if not isinstance(msg, dict) and hasattr(msg, "reasoning_content") and msg.reasoning_content:
            thinking = msg.reasoning_content
        else:
            # Some providers expose raw dict fields
            if not isinstance(msg, dict) and hasattr(msg, "model_dump"):
                data = msg.model_dump(exclude_none=True)
            else:
                data = msg if isinstance(msg, dict) else (getattr(msg, "__dict__", {}) or {})
            if isinstance(data, dict):
                thinking = data.get("reasoning_content") or data.get("thinking") or ""
    except Exception:
        thinking = ""

    if thinking:
        print("\n[THINKING] (truncated)\n", str(thinking)[:800])


if __name__ == "__main__":  # pragma: no cover
    main()
