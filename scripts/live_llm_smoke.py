#!/usr/bin/env python3
"""Live LLM smoke: drive one real ReAct turn through the Production Suite stack.

This proves the LLM provider + MuninAgent ReAct loop + tools + durable store
work end-to-end from an authenticated HTTP request, the same path the GUI uses:

    fixture user login (cookie + csrf) -> create conversation
    -> POST /api/chat                    (SSE stream, run executes inline)
    -> parse run_state envelopes          (until terminal state)

The Production API must be running on MUNIN_PRODUCTION_API_URL (default
http://127.0.0.1:8787).  The CI workflow pre-creates a per-run fixture user
and exports its credentials via MUNIN_LIVE_SMOKE_ADMIN / MUNIN_LIVE_SMOKE_PASSWORD
(``bootstrap_admin`` is global-once on the shared Turso, so the smoke must not
depend on it).

Provider resilience strategy: the run is retried up to ``LLM_SMOKE_MAX_ATTEMPTS``
times (default 3) with exponential backoff when the failure signature is
"provider-side" (run state=failed AND the failure message matches a known
transient provider pattern — 503/429/timeout/connection reset).  A failure whose
signature indicates a real Munin code bug completes the smoke as a hard failure
on the first attempt, so CI turns red on regressions without masking them.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = os.environ.get("MUNIN_PRODUCTION_API_URL", "http://127.0.0.1:8787").rstrip("/")
ORIGIN = os.environ.get("MUNIN_LIVE_SMOKE_ORIGIN", "http://127.0.0.1:8787")

ADMIN_USERNAME = os.environ.get("MUNIN_LIVE_SMOKE_ADMIN", "live-llm-smoke")
ADMIN_PASSWORD = os.environ.get("MUNIN_LIVE_SMOKE_PASSWORD", "live-llm-smoke-strong-password-v1")
# A plain recon prompt against fixtures the e2e_lab already seeds (cn=WEB01 is
# a device objectClass seeded by scripts/ldap_seed/60-web-lab.ldif).  Recon-only
# keeps the run deterministic: no state changes upstream that could make the
# agent's path depend on prior runs.
DEFAULT_PROMPT = os.environ.get(
    "MUNIN_LIVE_SMOKE_PROMPT",
    "Inspect the infrastructure belonging to the team-web device WEB01 and report "
    "which service listens on its port. Use an ldap_search to read the WEB01 record "
    "from dc=akatsuki,dc=com first, then use the httpx_probe tool explicitly against "
    "the service host/port it documents (do not substitute nmap_scan). "
    "Summarize what you found in 3-4 sentences at the end.",
)

RUN_DEADLINE_SECONDS = int(os.environ.get("MUNIN_LIVE_SMOKE_RUN_DEADLINE_SECONDS", "600"))
MAX_ATTEMPTS = int(os.environ.get("MUNIN_LIVE_SMOKE_MAX_ATTEMPTS", "3"))
BACKOFF_BASE_SECONDS = float(os.environ.get("MUNIN_LIVE_SMOKE_BACKOFF_BASE", "8.0"))
BACKOFF_MAX_SECONDS = float(os.environ.get("MUNIN_LIVE_SMOKE_BACKOFF_MAX", "60.0"))
REQUIRED_TOOL_NAMES = frozenset(
    name.strip()
    for name in os.environ.get("MUNIN_LIVE_SMOKE_REQUIRED_TOOLS", "ldap_search,httpx_probe").split(",")
    if name.strip()
)

# Regex of failure messages the dispatcher writes into complete_run when the
# provider is at fault.  Anything else is treated as a Munin code regression.
PROVIDER_TRANSIENT = re.compile(
    r"(timed?\s*out|timeout|connection\s*reset|429|503|502|504|"
    r"overloaded|rate\s*limit|temporarily|retry|EOF|ChunkedEncodingError|"
    r"ConnectionError|ConnectionResetError|RemoteProtocolError|ConnectError)",
    re.IGNORECASE,
)


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int, body: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _request(
    method: str,
    path: str,
    *,
    jar: http.cookiejar.CookieJar,
    csrf_token: str | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {
        "Accept": "application/json",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
    }
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
        raise HttpError(f"{method} {path} -> {exc.code}: {body[:400]}", exc.code, body) from exc


def _await_health(deadline: float = 90.0) -> None:
    """Poll /health until the Production API is up."""
    end = time.monotonic() + deadline
    last: Exception | None = None
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=5) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1)
    raise RuntimeError(f"Production API never became healthy: {last}")


def _login(jar: http.cookiejar.CookieJar) -> str:
    # The CI workflow pre-creates a per-run fixture user (shared Turso admin
    # is global-once, so bootstrap cannot be relied on).  For a fresh local
    # database the fixture user is bootstrapped by the operator beforehand.
    login = _request(
        "POST",
        "/api/auth/login",
        jar=jar,
        json_body={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    csrf = login["csrf_token"]
    # The session endpoint rotates the csrf cookie; pulling the freshest value
    # avoids stale-token rejection on the very first mutating request.
    session = _request("GET", "/api/auth/session", jar=jar, csrf_token=csrf)
    return str(session["csrf_token"])


def _create_conversation(jar: http.cookiejar.CookieJar, csrf: str, title: str) -> str:
    body: dict[str, Any] = {"title": title}
    test_run_id = os.environ.get("MUNIN_E2E_TEST_RUN_ID", "")
    if test_run_id:
        body["tags"] = ["created_by_test", test_run_id]
        body["scope"] = {"test_run_id": test_run_id, "created_by_test": True}
    result = _request(
        "POST",
        "/api/conversations",
        jar=jar,
        csrf_token=csrf,
        json_body=body,
    )
    return str(result["data"]["id"])


def _post_chat(jar: http.cookiejar.CookieJar, csrf: str, conversation_id: str, content: str) -> tuple[str, list[dict[str, Any]]]:
    """POST /api/chat and return ``(run_id, envelopes)``.

    Fase 3 (issue #9): the turn is executed inline by the request handler and
    streamed back as SSE envelopes.  The client reads until the ``close``
    event; the terminal ``run_state`` envelope carries the outcome.
    """
    idempotency = f"live-smoke-{int(time.time() * 1000)}"
    url = f"{BASE_URL}/api/chat"
    headers = {
        "Accept": "application/json, text/event-stream",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrf,
        "Idempotency-Key": idempotency,
        "Content-Type": "application/json",
        "Content-Length": str(len(json.dumps({"conversation_id": conversation_id, "content": content}).encode())),
    }
    req = urllib.request.Request(url, data=json.dumps({"conversation_id": conversation_id, "content": content}).encode(), headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        with opener.open(req, timeout=RUN_DEADLINE_SECONDS + 60) as resp:
            run_id = resp.headers.get("X-Munin-Run-Id", "")
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
        raise HttpError(f"POST /api/chat -> {exc.code}: {body[:400]}", exc.code, body) from exc
    return run_id, _parse_sse_envelopes(raw)


def _parse_sse_envelopes(raw: str) -> list[dict[str, Any]]:
    """Parse only Munin run-event frames from an AI SDK-compatible SSE body."""
    envelopes: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        lines = block.splitlines()
        event = next((line[7:] for line in lines if line.startswith("event: ")), "")
        data_lines = [line[6:] for line in lines if line.startswith("data: ")]
        if event == "run-event" and data_lines:
            envelopes.append(json.loads(data_lines[0]))
    return envelopes


def _resume_chat(
    jar: http.cookiejar.CookieJar, *, conversation_id: str
) -> list[dict[str, Any]]:
    """Replay a waiting/resumed run through the canonical AI SDK stream route.

    Resolving a HITL request wakes the detached runner asynchronously.  The
    first replay request can therefore legitimately return ``204 No Content``
    while the checkpoint is being resumed.  Poll until the durable stream has
    events instead of treating that short hand-off window as a failed run.
    """
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    deadline = time.monotonic() + RUN_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        req = urllib.request.Request(
            f"{BASE_URL}/api/chat/{conversation_id}/stream",
            headers={
                "Accept": "application/json, text/event-stream",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
            },
            method="GET",
        )
        try:
            with opener.open(req, timeout=min(RUN_DEADLINE_SECONDS + 60, 90)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
            raise HttpError(f"GET /api/chat/{conversation_id}/stream -> {exc.code}: {body[:400]}", exc.code, body) from exc
        envelopes = _parse_sse_envelopes(raw)
        if envelopes:
            return envelopes
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    raise RuntimeError(
        f"GET /api/chat/{conversation_id}/stream returned no replay events before the deadline"
    )


def _approve_pending_human_requests(
    jar: http.cookiejar.CookieJar,
    *,
    csrf: str,
    conversation_id: str,
    envelopes: list[dict[str, Any]],
    resolved_ids: set[str],
) -> list[dict[str, Any]]:
    """Resolve native Deep Agents HITL cards and return the durable replay."""
    pending = [
        item
        for item in envelopes
        if item.get("kind") == "human_request"
        and str(item.get("request_id") or "") not in resolved_ids
    ]
    if not pending:
        return []
    for request in pending:
        request_id = str(request.get("request_id") or "")
        nonce = str(request.get("nonce") or "")
        if not request_id or not nonce:
            raise RuntimeError("HITL envelope did not contain a resolvable request id and nonce")
        choices = [str(choice) for choice in request.get("choices") or []]
        choice = next((item for item in choices if item.lower() in {"approve", "approved"}), "approve")
        _request(
            "POST",
            f"/api/human-requests/{request_id}/resolve",
            jar=jar,
            csrf_token=csrf,
            json_body={"choice": choice, "nonce": nonce},
        )
        resolved_ids.add(request_id)
    return _resume_chat(jar, conversation_id=conversation_id)


def _terminal_state(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    for envelope in reversed(envelopes):
        if envelope.get("kind") == "run_state" and envelope.get("state") in {"completed", "failed", "interrupted", "cancelled"}:
            return envelope
    return {}


def _tool_calls(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [envelope for envelope in envelopes if envelope.get("kind") == "tool_intent"]


def _assistant_text(envelopes: list[dict[str, Any]]) -> str:
    return "".join(
        str(envelope.get("text") or "")
        for envelope in envelopes
        if envelope.get("kind") == "assistant_text"
    ).strip()


def _validate_completed_run(outcome: dict[str, Any]) -> None:
    """Assert the live agent used its catalog and emitted a final answer.

    A terminal state alone is not a ReAct smoke: it can be a model that chose
    to answer from memory, or a middleware path that ended before the planned
    probes. Tool names are deliberately the only diagnostics retained here;
    arguments and provider thinking are not logged by CI.
    """
    observed = {str(item.get("tool_name") or "") for item in outcome.get("tools") or []}
    missing = sorted(REQUIRED_TOOL_NAMES - observed)
    if missing:
        raise RuntimeError(
            "completed run did not invoke required tools: "
            f"missing={missing}; observed={sorted(name for name in observed if name)}"
        )
    if not str(outcome.get("answer") or "").strip():
        raise RuntimeError("completed run did not emit a final assistant answer")


def _run_one_attempt(prompt: str) -> dict[str, Any]:
    """One full try; raises on hard failure, returns the terminal run on success."""
    jar = http.cookiejar.CookieJar()
    _await_health()
    csrf = _login(jar)
    conversation_id = _create_conversation(jar, csrf, "Live LLM smoke")
    run_id, envelopes = _post_chat(jar, csrf, conversation_id, prompt)
    # Active tools use native Deep Agents interrupts. Approve each durable
    # request through the authenticated endpoint, then resume from the
    # persisted LangGraph checkpoint instead of fabricating a tool result.
    resolved_human_request_ids: set[str] = set()
    for _ in range(4):
        terminal = _terminal_state(envelopes)
        if terminal:
            break
        if not any(item.get("kind") == "human_request" for item in envelopes):
            break
        envelopes.extend(
            _approve_pending_human_requests(
                jar,
                csrf=csrf,
                conversation_id=conversation_id,
                envelopes=envelopes,
                resolved_ids=resolved_human_request_ids,
            )
        )
    terminal = _terminal_state(envelopes)
    tools = _tool_calls(envelopes)
    answer = _assistant_text(envelopes)
    print(f"::notice::live-llm-smoke: turn finished run_id={run_id} state={terminal.get('state', 'unknown')} tools={len(tools)}")
    return {
        "run": terminal,
        "tools": tools,
        "answer": answer,
        "conversation_id": conversation_id,
        "run_id": run_id,
    }


def _classify_failure(outcome: dict[str, Any]) -> str:
    """Return 'transient' | 'hard' based on the terminal run's failure signature."""
    terminal = outcome.get("run", {}) if outcome else {}
    state = str(terminal.get("state", ""))
    # If we never got a terminal run, the failure is in our own plumbing.
    if not terminal:
        return "hard"
    if state != "failed":
        # interrupted/cancelled are not provider-side; treat as hard.
        return "hard"
    # The dispatcher writes the exception text into the run_state envelope's
    # ``error`` field; grep it for the provider-transient vocabulary.
    blob = str(terminal.get("error") or "")
    if PROVIDER_TRANSIENT.search(blob):
        return "transient"
    # Heuristic: if the run produced zero tool calls AND failed, the failure
    # happened before the ReAct loop got going — almost always provider/auth.
    # This is the realistic shape of provider-side outages (TLS/auth/quota/
    # feed) since MuninAgent.respond raises before any tool.
    return "hard"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    last_failure: dict[str, Any] | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            outcome = _run_one_attempt(args.prompt)
        except (HttpError, OSError, RuntimeError) as exc:
            print(f"::warning::live-llm-smoke: attempt {attempt}/{MAX_ATTEMPTS} raised: {exc}")
            last_failure = {"run": {}, "detail": {}, "exc": str(exc)}
            classification = "transient" if PROVIDER_TRANSIENT.search(str(exc)) else "hard"
        else:
            run = outcome["run"]
            if run.get("state") == "completed":
                tools = outcome.get("tools") or []
                print(f"::notice::live-llm-smoke: run {outcome['run_id']} completed with {len(tools)} tool call(s)")
                _validate_completed_run(outcome)
                print("OK live LLM ReAct turn completed end-to-end")
                return 0
            last_failure = outcome
            classification = _classify_failure(outcome)
            print(f"::warning::live-llm-smoke: attempt {attempt}/{MAX_ATTEMPTS} ended in state={run.get('state')} ({classification})")

        if classification == "hard":
            break
        if attempt < MAX_ATTEMPTS:
            delay = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            print(f"::notice::live-llm-smoke: provider-side failure, backing off {delay:.1f}s before retry")
            time.sleep(delay)

    # All attempts exhausted (or hard failure).  Emit a structured failure.
    print("::error::live-llm-smoke: all attempts failed; last terminal state below")
    if last_failure:
        run = last_failure.get("run") or {}
        if run:
            print(f"::error::  run_id={last_failure.get('run_id')} state={run.get('state')}")
            if run.get("error"):
                print(f"::error::  terminal_error={run['error']}")
        if last_failure.get("exc"):
            print(f"::error::  exception: {last_failure['exc']}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"::error::live-llm-smoke unexpected: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
