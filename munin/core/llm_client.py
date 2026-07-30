"""OpenAI-compatible LLM client with adaptive timeout.

Accepts any provider that exposes an OpenAI-compatible `/v1/chat/completions`
endpoint: OpenAI, NVIDIA NIM, Groq, vLLM, Ollama (via /v1), TogetherAI, etc.

Timeout policy
--------------
Every real call is timed. We keep an exponentially-smoothed EMA of the successful
latencies per model (init: floor). The effective per-request timeout is

    timeout = clamp(floor, ceiling, max(floor, ema * 2.5))

with floor and ceiling read from :class:`Settings`. If a request times out, the
ceiling for the next attempt is bumped 25 % (still capped by ``timeout_ceiling``).
Never below 40 s, per project decision. This keeps small models snappy and lets big
NIM models finish long generations without hard-timing-out at 30 s.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError

from ..mcp.config import Settings

logger = logging.getLogger("munin.llm")

# LAN + metadata endpoints we do NOT want anyone accidentally pointing base_url at.
_BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.aws.internal",
    "0.0.0.0",
}
_ALLOWED_LOOPBACK = {"localhost", "127.0.0.1", "host.docker.internal", "::1"}


class LLMConfigError(RuntimeError):
    pass


def _validate_base_url(url: str) -> None:
    if not url:
        raise LLMConfigError("LLM_BASE_URL is empty")
    parsed = urlparse(url)
    if parsed.scheme == "https":
        if parsed.hostname and parsed.hostname.lower() in _BLOCKED_HOSTS:
            raise LLMConfigError(f"LLM_BASE_URL host is blocked: {parsed.hostname}")
        return
    if parsed.scheme == "http" and parsed.hostname and parsed.hostname.lower() in _ALLOWED_LOOPBACK:
        return
    raise LLMConfigError(f"LLM_BASE_URL must be https:// (or http:// on loopback). Got: {url}")


@dataclass
class _TimeoutState:
    ema_latency: float
    ceiling_bump: float = 1.0
    latencies: list[float] = field(default_factory=list)  # last 20


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        _validate_base_url(settings.llm_base_url)
        if not settings.llm_api_key:
            raise LLMConfigError("LLM_API_KEY is empty")
        if not settings.llm_model:
            raise LLMConfigError("LLM_MODEL is empty")
        self.settings = settings
        self._client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
        self._timeout = _TimeoutState(ema_latency=float(settings.llm_timeout_floor))

    # ------------------------------------------------------------------
    # Adaptive timeout
    # ------------------------------------------------------------------
    def _compute_timeout(self) -> float:
        floor = float(self.settings.llm_timeout_floor)
        ceiling = float(self.settings.llm_timeout_ceiling) * self._timeout.ceiling_bump
        base = max(floor, self._timeout.ema_latency * 2.5)
        return max(floor, min(ceiling, base))

    def _record_latency(self, elapsed: float) -> None:
        alpha = 0.3
        self._timeout.ema_latency = alpha * elapsed + (1 - alpha) * self._timeout.ema_latency
        self._timeout.latencies.append(elapsed)
        if len(self._timeout.latencies) > 20:
            self._timeout.latencies.pop(0)
        # Reset bump on any success
        self._timeout.ceiling_bump = 1.0

    def _bump_ceiling(self) -> None:
        # 25 % increase per timeout, capped at 3x
        self._timeout.ceiling_bump = min(3.0, self._timeout.ceiling_bump * 1.25)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """One chat/completions call with adaptive timeout + retry.

        Returns the OpenAI response as a dict (via .model_dump()).
        """
        effective_model = model or self.settings.llm_model
        attempt = 0
        last_exc: Exception | None = None
        while attempt < max_retries:
            attempt += 1
            timeout = self._compute_timeout()
            logger.debug("LLM call attempt=%d timeout=%.1fs model=%s", attempt, timeout, effective_model)
            started = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": effective_model,
                    "messages": messages,
                    "temperature": temperature,
                    "timeout": timeout,
                }
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                response = self._client.chat.completions.create(**kwargs)
                elapsed = time.monotonic() - started
                self._record_latency(elapsed)
                return response.model_dump()
            except APITimeoutError as exc:
                last_exc = exc
                logger.warning("LLM timeout attempt=%d elapsed=%.1fs; bumping ceiling and retrying", attempt, time.monotonic() - started)
                self._bump_ceiling()
                time.sleep(min(30.0, 2**attempt))
            except (APIConnectionError, APIStatusError) as exc:
                last_exc = exc
                logger.warning("LLM error attempt=%d: %s", attempt, exc)
                time.sleep(min(30.0, 2**attempt))
        assert last_exc is not None
        raise last_exc

    def make_langchain(self) -> Any:
        """Return a ChatOpenAI instance wired to the same base_url/api_key/model.

        Provided so LangGraph create_react_agent can consume it directly. Imported
        lazily so users of LLMClient without LangChain (e.g. quick scripts) don't
        pay the import cost.
        """
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.settings.llm_model,
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            temperature=0.2,
            timeout=self._compute_timeout(),
            max_retries=3,
        )
