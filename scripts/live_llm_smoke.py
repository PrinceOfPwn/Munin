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
    "from dc=akatsuki,dc=com first, then probe the service host/port it documents. "
    "Summarize what you found in 3-4 sentences at the end.",
)

RUN_DEADLINE_SECONDS = int(os.environ.get("MUNIN_LIVE_SMOKE_RUN_DEADLINE_SECONDS", "600"))
MAX_ATTEMPTS = int(os.environ.get("MUNIN_LIVE_SMOKE_MAX_ATTEMPTS", "3"))
BACKOFF_BASE_SECONDS = float(os.environ.get("MUNIN_LIVE_SMOKE_BACKOFF_BASE", "8.0"))
BACKOFF_MAX_SECONDS = float(os.environ.get("MUNIN_LIVE_SMOKE_BACKOFF_MAX", "60.0"))

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
    envelopes: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        lines = block.splitlines()
        event = next((line[7:] for line in lines if line.startswith("event: ")), "")
        data_lines = [line[6:] for line in lines if line.startswith("data: ")]
        if event == "run-event" and data_lines:
            envelopes.append(json.loads(data_lines[0]))
    return run_id, envelopes


def _terminal_state(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    for envelope in reversed(envelopes):
        if envelope.get("kind") == "run_state" and envelope.get("state") in {"completed", "failed", "interrupted", "cancelled"}:
            return envelope
    return {}


def _tool_calls(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [envelope for envelope in envelopes if envelope.get("kind") == "tool_intent"]


def _run_one_attempt(prompt: str) -> dict[str, Any]:
    """One full try; raises on hard failure, returns the terminal run on success."""
    jar = http.cookiejar.CookieJar()
    _await_health()
    csrf = _login(jar)
    conversation_id = _create_conversation(jar, csrf, "Live LLM smoke")
    run_id, envelopes = _post_chat(jar, csrf, conversation_id, prompt)
    terminal = _terminal_state(envelopes)
    tools = _tool_calls(envelopes)
    print(f"::notice::live-llm-smoke: turn finished run_id={run_id} state={terminal.get('state', 'unknown')} tools={len(tools)}")
    return {"run": terminal, "tools": tools, "conversation_id": conversation_id, "run_id": run_id}


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
    tools = outcome.get("tools", []) or []
    if not tools:
        return "transient"
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
                if not tools:
                    print("::warning::live-llm-smoke: completed run produced 0 tool calls; the agent may not have invoked the catalog")
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
        if last_failure.get("exc"):
            print(f"::error::  exception: {last_failure['exc']}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"::error::live-llm-smoke unexpected: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
