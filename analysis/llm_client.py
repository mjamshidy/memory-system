"""
llm_client.py — Configurable LLM factory.

Supports Claude (Anthropic), OpenAI (GPT), Google (Gemini), Ollama (local).

Usage:
    from analysis.llm_client import get_llm
    llm = get_llm("claude:claude-haiku-4-5-20251001")
    response = await llm.complete("Summarise this: ...")
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class BaseLLM(ABC):
    """Abstract LLM wrapper."""

    @abstractmethod
    async def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
        ...

    @abstractmethod
    async def complete_json(self, prompt: str, system: str = "", max_tokens: int = 2048) -> dict:
        """Return JSON-parsed response."""
        ...


# ---------------------------------------------------------------------------
# Anthropic / Claude
# ---------------------------------------------------------------------------

class AnthropicLLM(BaseLLM):
    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        import anthropic
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    async def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
        kwargs: dict = {"model": self.model, "max_tokens": max_tokens}
        if system:
            kwargs["system"] = system
        kwargs["messages"] = [{"role": "user", "content": prompt}]
        resp = await self.client.messages.create(**kwargs)
        return resp.content[0].text

    async def complete_json(self, prompt: str, system: str = "", max_tokens: int = 2048) -> dict:
        import json
        sys = (system + "\n" if system else "") + "Respond with valid JSON only. No markdown fences."
        text = await self.complete(prompt, system=sys, max_tokens=max_tokens)
        text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(text)


# ---------------------------------------------------------------------------
# OpenAI / GPT
# ---------------------------------------------------------------------------

class OpenAILLM(BaseLLM):
    def __init__(self, model: str = "gpt-4o-mini"):
        from openai import AsyncOpenAI
        self.model = model
        self.client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    async def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages, max_tokens=max_tokens
        )
        return resp.choices[0].message.content or ""

    async def complete_json(self, prompt: str, system: str = "", max_tokens: int = 2048) -> dict:
        import json
        sys = (system + "\n" if system else "") + "Respond with valid JSON only."
        messages = []
        if sys:
            messages.append({"role": "system", "content": sys})
        messages.append({"role": "user", "content": prompt})
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content or "{}")


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

class GeminiLLM(BaseLLM):
    def __init__(self, model: str = "gemini-1.5-flash"):
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
        self.model_obj = genai.GenerativeModel(model)

    async def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
        import asyncio
        full_prompt = (f"{system}\n\n{prompt}") if system else prompt
        loop = asyncio.get_event_loop()
        # Gemini SDK is sync — run in executor
        resp = await loop.run_in_executor(
            None, lambda: self.model_obj.generate_content(full_prompt)
        )
        return resp.text

    async def complete_json(self, prompt: str, system: str = "", max_tokens: int = 2048) -> dict:
        import json
        text = await self.complete(
            prompt, system=(system + "\nRespond with valid JSON only.") if system else "Respond with valid JSON only."
        )
        text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(text)


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

class OllamaLLM(BaseLLM):
    def __init__(self, model: str = "llama3.1"):
        import httpx
        self.model = model
        self.base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=120)

    async def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        resp = await self.client.post("/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "")

    async def complete_json(self, prompt: str, system: str = "", max_tokens: int = 2048) -> dict:
        import json
        sys = (system + "\n" if system else "") + "Respond with valid JSON only."
        text = await self.complete(prompt, system=sys, max_tokens=max_tokens)
        text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(text)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDER_MAP = {
    "claude":    AnthropicLLM,
    "anthropic": AnthropicLLM,
    "openai":    OpenAILLM,
    "gpt":       OpenAILLM,
    "gemini":    GeminiLLM,
    "google":    GeminiLLM,
    "ollama":    OllamaLLM,
    "local":     OllamaLLM,
}


def get_llm(spec: str) -> BaseLLM:
    """
    Parse a provider:model spec and return the appropriate LLM instance.

    Examples:
      get_llm("claude:claude-haiku-4-5-20251001")
      get_llm("openai:gpt-4o")
      get_llm("gemini:gemini-1.5-pro")
      get_llm("ollama:llama3.1")
      get_llm("claude")   # uses default model
    """
    if ":" in spec:
        provider, model = spec.split(":", 1)
    else:
        provider = spec
        model = None

    cls = _PROVIDER_MAP.get(provider.lower())
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Options: {list(_PROVIDER_MAP)}")

    if model:
        return cls(model=model)
    return cls()
