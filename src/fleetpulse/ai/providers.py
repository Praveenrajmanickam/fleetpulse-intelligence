"""Provider abstraction. Application logic depends only on ``AIProvider``.

Configuration (environment only; keys never reach the frontend, logs, audit or responses):
    FLEETPULSE_AI_PROVIDER   none | mock | anthropic | openai_compatible   (unset = none)
    FLEETPULSE_AI_MODEL      model name (provider default if unset)
    FLEETPULSE_AI_API_KEY    secret
    FLEETPULSE_AI_BASE_URL   optional endpoint override (openai_compatible: required)
    FLEETPULSE_AI_TIMEOUT_S  per-call timeout, default 30
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


class ProviderUnavailable(RuntimeError):
    """No provider configured (or misconfigured): the analyst is off, the dashboard is not."""


class ProviderError(RuntimeError):
    """The provider call failed (network, HTTP error, malformed reply, timeout)."""


@dataclass(frozen=True)
class ProviderRequest:
    system: str
    user: str  # evidence envelope + delimited question (built by prompt.py)
    # structured copies for deterministic (mock) providers; real providers use only system/user
    scope: str
    intent: str
    package: object  # EvidencePackage
    flags: tuple[str, ...] = ()
    corrective_note: str | None = None


@dataclass(frozen=True)
class ProviderReply:
    text: str
    model: str


class AIProvider(ABC):
    name: str = "abstract"
    is_mock: bool = False

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def generate(self, request: ProviderRequest, timeout_s: float) -> ProviderReply: ...

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model, "is_mock": self.is_mock}

    def __repr__(self) -> str:  # never include secrets
        return f"{type(self).__name__}(model={self.model!r})"


class MockAIProvider(AIProvider):
    """Deterministic development provider. NOT an LLM: it composes the answer from the evidence
    package with fixed templates, so the full pipeline can be tested without API cost."""
    name = "mock"
    is_mock = True

    def __init__(self, model: str = "fleetpulse-mock-v1"):
        super().__init__(model)

    def generate(self, request: ProviderRequest, timeout_s: float) -> ProviderReply:
        from fleetpulse.ai.compose import compose_answer

        text = compose_answer(request.package, request.intent, request.flags)
        return ProviderReply(text=json.dumps({"answer": text}), model=self.model)


class _HttpProvider(AIProvider):
    def __init__(self, model: str, api_key: str, base_url: str, transport: httpx.BaseTransport | None = None):
        super().__init__(model)
        if not api_key:
            raise ProviderUnavailable(f"{self.name}: FLEETPULSE_AI_API_KEY is not set")
        self.__key = api_key  # name-mangled; never serialised
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    def _secret(self) -> str:
        return self.__key

    def _post(self, url: str, headers: dict, body: dict, timeout_s: float) -> dict:
        try:
            with httpx.Client(timeout=timeout_s, transport=self._transport) as c:
                r = c.post(url, headers=headers, json=body)
        except httpx.TimeoutException:
            raise ProviderError(f"{self.name}: timeout after {timeout_s:g} s") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: transport error ({type(exc).__name__})") from None
        if r.status_code >= 400:
            raise ProviderError(f"{self.name}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise ProviderError(f"{self.name}: non-JSON reply") from None


class AnthropicProvider(_HttpProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.anthropic.com", transport=None):
        super().__init__(model, api_key, base_url, transport)

    def generate(self, request: ProviderRequest, timeout_s: float) -> ProviderReply:
        body = {"model": self.model, "max_tokens": 1200, "system": request.system,
                "messages": [{"role": "user", "content": request.user}]}
        headers = {"x-api-key": self._secret(), "anthropic-version": "2023-06-01", "content-type": "application/json"}
        data = self._post(f"{self.base_url}/v1/messages", headers, body, timeout_s)
        try:
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError):
            raise ProviderError("anthropic: unexpected reply shape") from None
        return ProviderReply(text=text, model=data.get("model", self.model))


class OpenAICompatibleProvider(_HttpProvider):
    name = "openai_compatible"

    def generate(self, request: ProviderRequest, timeout_s: float) -> ProviderReply:
        body = {"model": self.model, "max_tokens": 1200, "temperature": 0,
                "messages": [{"role": "system", "content": request.system}, {"role": "user", "content": request.user}]}
        headers = {"Authorization": f"Bearer {self._secret()}", "content-type": "application/json"}
        data = self._post(f"{self.base_url}/chat/completions", headers, body, timeout_s)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("openai_compatible: unexpected reply shape") from None
        return ProviderReply(text=text or "", model=data.get("model", self.model))


DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "mock": "fleetpulse-mock-v1"}


def resolve_provider(env: dict | None = None) -> tuple[AIProvider | None, str]:
    """(provider, status message). provider None = analyst unavailable; the dashboard still runs."""
    env = os.environ if env is None else env
    kind = (env.get("FLEETPULSE_AI_PROVIDER") or "none").strip().lower()
    model = (env.get("FLEETPULSE_AI_MODEL") or "").strip()
    key = env.get("FLEETPULSE_AI_API_KEY") or ""
    base = (env.get("FLEETPULSE_AI_BASE_URL") or "").strip()
    if kind in ("", "none", "off", "disabled"):
        return None, "No AI provider configured (set FLEETPULSE_AI_PROVIDER)."
    if kind == "mock":
        return MockAIProvider(model or DEFAULT_MODELS["mock"]), "Development mock provider (deterministic, not an LLM)."
    if kind not in ("anthropic", "openai_compatible", "openai"):
        return None, f"Unknown FLEETPULSE_AI_PROVIDER {kind!r}."
    if not key:
        return None, f"Provider {kind!r} configured but FLEETPULSE_AI_API_KEY is not set."
    if kind == "anthropic":
        return AnthropicProvider(model or DEFAULT_MODELS["anthropic"], key, base or "https://api.anthropic.com"), "ok"
    if not model or not base:
        return None, "openai_compatible requires FLEETPULSE_AI_MODEL and FLEETPULSE_AI_BASE_URL."
    return OpenAICompatibleProvider(model, key, base), "ok"


def timeout_from_env(env: dict | None = None) -> float:
    env = os.environ if env is None else env
    try:
        return max(1.0, float(env.get("FLEETPULSE_AI_TIMEOUT_S", "30")))
    except ValueError:
        return 30.0
