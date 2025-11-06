from typing import Iterable
from openai import OpenAI


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float | int = 15.0):
        # SiliconFlow uses OpenAI-compatible interface
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        self.client = OpenAI(base_url=base, api_key=api_key, timeout=timeout)
        self.model = model
        self.timeout = timeout

    def summarize(self, text: str, lang: str = "zh-CN") -> str:
        sys = "You are an expert summarizer. Output a concise bullet list in target language."
        user = f"Summarize to {lang}. Keep 6 bullets max. Text:\n{text}"
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            timeout=self.timeout,
        )
        return resp.choices[0].message.content or ""