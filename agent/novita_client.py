"""
Novita AI Client Wrapper (novita_client.py)

Uses the official `openai` SDK (from openai import OpenAI) with base_url="https://api.novita.ai/v3/openai"
to send ChatCompletion API requests to Novita AI for models like Kimi K-3 (moonshotai/kimi-k3).
Includes fallback to urllib.request if OpenAI package is not installed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
    HAS_OPENAI_SDK = True
except ImportError:
    HAS_OPENAI_SDK = False


def sanitize_ascii(text: str) -> str:
    """Removes non-ASCII characters from headers/API keys."""
    if not text:
        return ""
    return "".join(c for c in text if ord(c) < 128).strip()


class NovitaAIClient:
    """Client for Novita AI using OpenAI SDK or urllib fallback."""

    DEFAULT_BASE_URL = "https://api.novita.ai/v3/openai"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "moonshotai/kimi-k3",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        top_p: float = 0.95,
    ):
        raw_key = api_key or os.environ.get("NOVITA_API_KEY", "")
        self.api_key = sanitize_ascii(raw_key)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.openai_client = None

        if HAS_OPENAI_SDK and self.is_configured():
            try:
                self.openai_client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            except Exception as e:
                print(f"[NovitaClient Warning] Could not initialize OpenAI SDK: {e}")

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def generate_chat_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
    ) -> str:
        if not self.is_configured():
            raise ValueError("NOVITA_API_KEY environment variable or api_key parameter is not set.")

        model_name = model or self.model
        temp_val = temperature if temperature is not None else self.temperature
        max_t_val = max_tokens if max_tokens is not None else self.max_tokens
        top_p_val = top_p if top_p is not None else self.top_p

        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # 1. Try official OpenAI SDK if available
        if self.openai_client:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": temp_val,
                    "max_tokens": max_t_val,
                    "top_p": top_p_val,
                }
                response = self.openai_client.chat.completions.create(**kwargs)
                if response.choices:
                    return response.choices[0].message.content or ""
                return ""
            except Exception as e:
                print(f"[NovitaClient] OpenAI SDK call failed ({e}), trying fallback URL endpoints...")

        # 2. HTTP urllib fallback attempting both standard base_urls if needed
        urls_to_try = [
            f"{self.base_url}/chat/completions" if not self.base_url.endswith("/chat/completions") else self.base_url,
            "https://api.novita.ai/v3/openai/chat/completions",
            "https://api.novita.ai/openai/chat/completions",
        ]

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temp_val,
            "max_tokens": max_t_val,
            "top_p": top_p_val,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        }

        last_error = None
        for endpoint in urls_to_try:
            req = urllib.request.Request(
                url=endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    choices = result.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "").strip()
                    return ""
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                last_error = f"Novita AI HTTP Error ({e.code}) at {endpoint}: {err_body}"
                if e.code == 401:
                    # If 401, try alternate endpoint URL in list
                    continue
                break
            except Exception as e:
                last_error = f"Novita AI Client Error at {endpoint}: {e}"

        raise RuntimeError(last_error or "Failed to connect to Novita AI.")
