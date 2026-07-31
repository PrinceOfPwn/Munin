"""OpenAI-compatible LLM client with adaptive timeout and live streaming.

Accepts any provider that exposes an OpenAI-compatible ``/v1/chat/completions``
endpoint.  When a production run installs an observer through
:mod:`munin.core.llm_stream`, the client requests a streaming completion and
emits provider-supplied reasoning plus assistant text deltas.  Callers without
an observer keep the original non-streaming behaviour.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from ..mcp.config import Settings
from .llm_stream import emit_llm_stream, has_llm_stream_observer

logger = logging.getLogger("munin.llm")

_BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.aws.internal",
    "0.0.0.0",
}
_ALLOWED_LOOPBACK = {"localhost", "127.0.0.1", "host.docker.internal", "::1"}
_REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking", "reasoning_summary")


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
    latencies: list[float] = field(default_factory=list)


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
        self._timeout.ceiling_bump = 1.0

    def _bump_ceiling(self) -> None:
        self._timeout.ceiling_bump = min(3.0, self._timeout.ceiling_bump * 1.25)

    @staticmethod
    def _merge_tool_delta(target: dict[int, dict[str, Any]], raw: Any) -> None:
        """Merge OpenAI tool-call deltas into a normal assistant message."""

        data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw or {})
        index = int(data.get("index") or 0)
        entry = target.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if data.get("id"):
            entry["id"] = str(data["id"])
        if data.get("type"):
            entry["type"] = str(data["type"])
        function = data.get("function") or {}
        if hasattr(function, "model_dump"):
            function = function.model_dump()
        if function.get("name"):
            entry["function"]["name"] += str(function["name"])
        if function.get("arguments"):
            entry["function"]["arguments"] += str(function["arguments"])

    def _stream_completion(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Consume a provider stream and reconstruct the legacy response shape."""

        kwargs["stream"] = True
        stream = self._client.chat.completions.create(**kwargs)
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        response_id = ""
        response_model = str(kwargs.get("model") or "")

        emit_llm_stream({"stage": "model_stream_started", "message": "Model stream connected"})
        for chunk in stream:
            payload = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
            response_id = str(payload.get("id") or response_id)
            response_model = str(payload.get("model") or response_model)
            choices = payload.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            finish_reason = choice.get("finish_reason") or finish_reason

            text = delta.get("content")
            if isinstance(text, str) and text:
                content_parts.append(text)
                emit_llm_stream({"stage": "assistant_delta", "delta": text, "message": text})

            for field in _REASONING_FIELDS:
                value = delta.get(field)
                if isinstance(value, str) and value:
                    reasoning_parts.append(value)
                    emit_llm_stream(
                        {
                            "stage": "provider_reasoning_delta",
                            "delta": value,
                            "message": value,
                            "provider_exposed": True,
                        }
                    )
                    break

            for call in delta.get("tool_calls") or []:
                self._merge_tool_delta(tool_calls, call)

        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        emit_llm_stream(
            {
                "stage": "model_stream_completed",
                "message": "Model stream completed",
                "finish_reason": finish_reason or "stop",
            }
        )
        return {
            "id": response_id,
            "model": response_model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason or "stop",
                }
            ],
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_retries: int | None = None,
        on_retry: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """One completion call with adaptive timeout, retry and optional streaming."""

        effective_model = model or self.settings.llm_model
        attempts = max(1, max_retries if max_retries is not None else self.settings.llm_retry_attempts)
        attempt = 0
        last_exc: Exception | None = None
        while attempt < attempts:
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
                response = (
                    self._stream_completion(kwargs)
                    if has_llm_stream_observer()
                    else self._client.chat.completions.create(**kwargs).model_dump()
                )
                self._record_latency(time.monotonic() - started)
                return response
            except APITimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "LLM timeout attempt=%d/%d elapsed=%.1fs; bumping ceiling",
                    attempt,
                    attempts,
                    time.monotonic() - started,
                )
                self._bump_ceiling()
                if attempt < attempts:
                    self._sleep_before_retry(attempt, attempts, "timeout", on_retry)
            except (APIConnectionError, APIStatusError) as exc:
                last_exc = exc
                status_code = getattr(exc, "status_code", None)
                if status_code is not None and status_code not in {408, 409, 429} and status_code < 500:
                    raise
                logger.warning(
                    "LLM transient error attempt=%d/%d status=%s: %s",
                    attempt,
                    attempts,
                    status_code,
                    exc,
                )
                if attempt < attempts:
                    self._sleep_before_retry(attempt, attempts, f"HTTP {status_code or 'connection error'}", on_retry)
        assert last_exc is not None
        raise last_exc

    def _sleep_before_retry(
        self,
        attempt: int,
        attempts: int,
        reason: str,
        on_retry: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        delay = min(
            self.settings.llm_retry_max_delay,
            self.settings.llm_retry_base_delay * (2 ** (attempt - 1)),
        )
        event = {
            "attempt": attempt,
            "max_attempts": attempts,
            "retry_in_seconds": delay,
            "reason": reason,
        }
        if on_retry is not None:
            try:
                on_retry(event)
            except Exception:
                logger.debug("LLM retry observer failed", exc_info=True)
        logger.info("LLM will retry in %.1fs after %s", delay, reason)
        time.sleep(delay)

    def make_langchain(self) -> Any:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.settings.llm_model,
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            temperature=0.2,
            timeout=self._compute_timeout(),
            max_retries=3,
        )
