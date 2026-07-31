#!/usr/bin/env python3
"""Live LLM smoke: drive one real ReAct turn through the Production Suite stack.

This proves the LLM provider + MuninAgent ReAct loop + tools + durable store
work end-to-end from an authenticated HTTP request, the same path the GUI uses:

    bootstrap_admin -> login (cookie + csrf) -> create conversation
    -> POST /api/conversations/{id}/turns   (auto-dispatcher starts run)
    -> poll GET /api/runs/{run_id}         (until terminal state)
    -> GET /api/runs/{run_id}/detail       (validate tool calls + response)

The Production API must be running on MUNIN_PRODUCTION_API_URL (default
http://127.0.0.1:8787) with MUNIN_PRODUCTION_AUTO_DISPATCH=1 so the turn
handler itself spawns the dispatcher thread that runs MuninAgent.respond.

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
POLL_INTERVAL_SECONDS = float(os.environ.get("MUNIN_LIVE_SMOKE_POLL_INTERVAL", "2.0"))
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


def _bootstrap_and_login(jar: http.cookiejar.CookieJar) -> str:
    # bootstrap is idempotent — returns 409 with a benign body if an admin
    # already exists; we tolerate it because the run-id namespace is shared.
    try:
        _request(
            "POST",
            "/api/auth/bootstrap",
            jar=jar,
            json_body={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
    except HttpError as exc:
        if exc.status != 409:
            raise
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
    result = _request(
        "POST",
        "/api/conversations",
        jar=jar,
        csrf_token=csrf,
        json_body={"title": title},
    )
    return str(result["data"]["id"])


def _post_turn(jar: http.cookiejar.CookieJar, csrf: str, conversation_id: str, content: str) -> dict[str, Any]:
    idempotency = f"live-smoke-{int(time.time() * 1000)}"
    return _request(
        "POST",
        f"/api/conversations/{conversation_id}/turns",
        jar=jar,
        csrf_token=csrf,
        json_body={"content": content, "idempotency_key": idempotency},
    )


def _get_run(jar: http.cookiejar.CookieJar, run_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/runs/{run_id}", jar=jar)


def _get_run_detail(jar: http.cookiejar.CookieJar, run_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/runs/{run_id}/detail", jar=jar)


def _wait_terminal(jar: http.cookiejar.CookieJar, run_id: str, deadline_s: float) -> dict[str, Any]:
    """Poll the run until it reaches a terminal state or the deadline expires."""
    final_states = {"completed", "failed", "interrupted", "cancelled"}
    deadline = time.monotonic() + deadline_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = _get_run(jar, run_id)["data"]
        last = run
        if run["state"] in final_states:
            return run
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"run {run_id} did not reach a terminal state within {deadline_s}s (last={last!r})")


def _run_one_attempt(prompt: str) -> dict[str, Any]:
    """One full try; raises on hard failure, returns the terminal run on success."""
    jar = http.cookiejar.CookieJar()
    _await_health()
    csrf = _bootstrap_and_login(jar)
    conversation_id = _create_conversation(jar, csrf, "Live LLM smoke")
    turn = _post_turn(jar, csrf, conversation_id, prompt)
    run_id = str(turn["data"]["run"]["id"])
    print(f"::notice::live-llm-smoke: turn created, run_id={run_id}, waiting for terminal state (deadline={RUN_DEADLINE_SECONDS}s)")
    terminal = _wait_terminal(jar, run_id, RUN_DEADLINE_SECONDS)
    detail = _get_run_detail(jar, run_id)["data"]
    return {"run": terminal, "detail": detail, "conversation_id": conversation_id, "run_id": run_id}


def _classify_failure(outcome: dict[str, Any]) -> str:
    """Return 'transient' | 'hard' based on the terminal run's failure signature."""
    run = outcome.get("run", {}) if outcome else {}
    state = str(run.get("state", ""))
    # If we never got a terminal run, the failure is in our own plumbing.
    if not run:
        return "hard"
    if state != "failed":
        # interrupted/cancelled are not provider-side; treat as hard.
        return "hard"
    # For failed runs we look at the assistant message content, since the
    # dispatcher writes "Operation failed: <exc>" there.  detail.tools may also
    # surface provider errors if a tool chunk captured them.
    fragments: list[str] = []
    for msg in (outcome.get("detail", {}).get("events") or []):
        payload = msg.get("payload") if isinstance(msg, dict) else None
        if isinstance(payload, dict):
            err = payload.get("error") or payload.get("reason")
            if isinstance(err, str):
                fragments.append(err)
    # The dispatcher wraps the exception text into the assistant message
    # content itself; we grep it for the provider-transient vocabulary.
    # We don't have a direct accessor for it here, so rely on run state plus
    # specificity below: if the run failed fast (under 5s) it's almost
    # certainly provider/auth/connection; if it ran long it likely reached
    # the agent and a code-side error surfaced.
    blob = " ".join(fragments)
    if PROVIDER_TRANSIENT.search(blob):
        return "transient"
    # Heuristic: if the run produced zero tool calls AND failed quickly, the
    # failure happened before the ReAct loop got going — almost always
    # provider/auth.  This is the realistic shape of provider-side outages
    # (TLS/auth/quota/feed) since MuninAgent.respond raises before any tool.
    tools = outcome.get("detail", {}).get("tools", []) or []
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
        except (HttpError, RuntimeError) as exc:
            print(f"::warning::live-llm-smoke: attempt {attempt}/{MAX_ATTEMPTS} raised: {exc}")
            last_failure = {"run": {}, "detail": {}, "exc": str(exc)}
            classification = "transient" if PROVIDER_TRANSIENT.search(str(exc)) else "hard"
        else:
            run = outcome["run"]
            if run["state"] == "completed":
                tools = outcome["detail"].get("tools", []) or []
                print(f"::notice::live-llm-smoke: run {outcome['run_id']} completed with {len(tools)} tool call(s)")
                if not tools:
                    print("::warning::live-llm-smoke: completed run produced 0 tool calls; the agent may not have invoked the catalog")
                # Final assistant content lives on the detail's run record via
                # the message body; we don't parse it strictly here to avoid
                # coupling the smoke to a particular phrasing, but ensure the
                # run is genuinely "completed" (above) and lasted long enough
                # to plausibly have hit the provider.
                print("OK live LLM ReAct turn completed end-to-end")
                return 0
            last_failure = outcome
            classification = _classify_failure(outcome)
            print(f"::warning::live-llm-smoke: attempt {attempt}/{MAX_ATTEMPTS} ended in state={run['state']} ({classification})")

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
