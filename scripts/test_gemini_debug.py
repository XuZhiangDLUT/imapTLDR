"""Debug script for Gemini API response format analysis.

This script tests the exact same API call that preflight_check_llm makes,
and dumps the full response structure to diagnose why it returns empty content.
"""
from __future__ import annotations
import json

from openai import OpenAI


# Configuration from config.json - bohe provider
BASE_URL = "https://x666.me/v1"
API_KEY = "sk-XKmR94WbOxg0FZwEHcuUihTTgN8h8yohGt3r75RjnxHKuixu"
MODEL = "gemini-2.5-pro-1m"
ENABLE_THINKING = True
THINKING_BUDGET = -1  # dynamic thinking


def dump_object(obj, name: str = "obj"):
    """Dump all attributes of an object for debugging."""
    print(f"\n{'='*60}")
    print(f"[{name}] type: {type(obj)}")
    print(f"{'='*60}")

    if obj is None:
        print("  (None)")
        return

    # Try to get all attributes
    if hasattr(obj, "__dict__"):
        print(f"  __dict__: {obj.__dict__}")

    if hasattr(obj, "model_dump"):
        try:
            dumped = obj.model_dump(exclude_none=True)
            print(f"  model_dump(): {json.dumps(dumped, ensure_ascii=False, indent=4)}")
        except Exception as e:
            print(f"  model_dump() error: {e}")

    # List all attributes
    attrs = [a for a in dir(obj) if not a.startswith("_")]
    print(f"  Public attributes: {attrs}")

    # Try to access common fields
    for attr in ["content", "reasoning_content", "thinking", "text", "message", "parts"]:
        if hasattr(obj, attr):
            val = getattr(obj, attr, None)
            print(f"  .{attr} = {repr(val)[:500]}")


def test_with_thinking():
    """Test with thinking enabled (same as preflight_check_llm)."""
    print("\n" + "="*80)
    print("TEST 1: With thinking enabled (thinking_budget=-1)")
    print("="*80)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=30.0)

    extra = {}
    extra["enable_thinking"] = True
    extra["thinking_budget"] = THINKING_BUDGET

    # Gemini thinking config
    gen_cfg = {}
    think_cfg = {"thinkingBudget": THINKING_BUDGET}
    gen_cfg["thinkingConfig"] = think_cfg
    extra["generationConfig"] = gen_cfg

    print(f"\nextra_body: {json.dumps(extra, indent=2)}")

    try:
        r = client.chat.completions.create(
            model=MODEL,
            temperature=0.0,
            messages=[{"role": "user", "content": "请计算 2+3 等于几？只回答数字。"}],
            max_tokens=32,
            timeout=30.0,
            extra_body=extra,
        )
    except Exception as e:
        print(f"\n[ERROR] Request failed: {repr(e)}")
        return

    dump_object(r, "response")

    if hasattr(r, "choices") and r.choices:
        choice = r.choices[0]
        dump_object(choice, "choice[0]")

        if hasattr(choice, "message"):
            msg = choice.message
            dump_object(msg, "message")

            # Extract content the same way preflight_check_llm does
            content = (msg.content or "") if hasattr(msg, "content") else ""
            content_stripped = content.strip() if content else ""

            print(f"\n[RESULT] message.content = {repr(content)}")
            print(f"[RESULT] content.strip() = {repr(content_stripped)}")
            print(f"[RESULT] bool(content_stripped) = {bool(content_stripped)}")

            if not content_stripped:
                print("\n[DIAGNOSIS] Content is empty! This is why preflight fails.")

                # Check for alternative fields
                for field in ["reasoning_content", "thinking", "text", "reasoning"]:
                    val = getattr(msg, field, None)
                    if val:
                        print(f"  Found content in .{field}: {repr(val)[:200]}")


def test_without_thinking():
    """Test without thinking enabled."""
    print("\n" + "="*80)
    print("TEST 2: Without thinking enabled")
    print("="*80)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=30.0)

    try:
        r = client.chat.completions.create(
            model=MODEL,
            temperature=0.0,
            messages=[{"role": "user", "content": "请计算 2+3 等于几？只回答数字。"}],
            max_tokens=32,
            timeout=30.0,
        )
    except Exception as e:
        print(f"\n[ERROR] Request failed: {repr(e)}")
        return

    if hasattr(r, "choices") and r.choices:
        msg = r.choices[0].message
        content = (msg.content or "") if hasattr(msg, "content") else ""
        content_stripped = content.strip() if content else ""

        print(f"\n[RESULT] message.content = {repr(content)}")
        print(f"[RESULT] content.strip() = {repr(content_stripped)}")
        print(f"[RESULT] bool(content_stripped) = {bool(content_stripped)}")


def test_with_thinking_positive_budget():
    """Test with thinking enabled and positive budget."""
    print("\n" + "="*80)
    print("TEST 3: With thinking enabled (thinking_budget=1024)")
    print("="*80)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=30.0)

    extra = {
        "enable_thinking": True,
        "thinking_budget": 1024,
        "generationConfig": {
            "thinkingConfig": {
                "thinkingBudget": 1024
            }
        }
    }

    print(f"\nextra_body: {json.dumps(extra, indent=2)}")

    try:
        r = client.chat.completions.create(
            model=MODEL,
            temperature=0.0,
            messages=[{"role": "user", "content": "请计算 2+3 等于几？只回答数字。"}],
            max_tokens=32,
            timeout=30.0,
            extra_body=extra,
        )
    except Exception as e:
        print(f"\n[ERROR] Request failed: {repr(e)}")
        return

    if hasattr(r, "choices") and r.choices:
        msg = r.choices[0].message
        dump_object(msg, "message")

        content = (msg.content or "") if hasattr(msg, "content") else ""
        content_stripped = content.strip() if content else ""

        print(f"\n[RESULT] message.content = {repr(content)}")
        print(f"[RESULT] content.strip() = {repr(content_stripped)}")


def test_raw_httpx():
    """Test using raw httpx to see exact response."""
    print("\n" + "="*80)
    print("TEST 4: Raw HTTP request to see exact JSON response")
    print("="*80)

    import httpx

    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": "请计算 2+3 等于几？只回答数字。"}],
        "max_tokens": 32,
        "generationConfig": {
            "thinkingConfig": {
                "thinkingBudget": -1
            }
        }
    }

    print(f"\nRequest URL: {url}")
    print(f"Request payload: {json.dumps(payload, indent=2)}")

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            print(f"\nStatus: {resp.status_code}")
            print(f"Response headers: {dict(resp.headers)}")

            try:
                data = resp.json()
                print(f"\nRaw JSON response:\n{json.dumps(data, ensure_ascii=False, indent=2)}")
            except:
                print(f"\nRaw text response:\n{resp.text}")
    except Exception as e:
        print(f"\n[ERROR] Request failed: {repr(e)}")


if __name__ == "__main__":
    test_with_thinking()
    test_without_thinking()
    test_with_thinking_positive_budget()
    test_raw_httpx()
