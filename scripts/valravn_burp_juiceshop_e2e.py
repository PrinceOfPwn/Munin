#!/usr/bin/env python3
"""Real Burp + OWASP Juice Shop smoke for the Valravn mesh.

This is intentionally an integration probe, not a vulnerability scanner. It
proves that a headless Burp instance has loaded the Valravn extension, that the
lab target is explicitly scoped, and that a Munin burp_* tool call traverses
Burp's proxy listener and lands in Proxy history.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request

import httpx


def _wait_http(url: str, *, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - fixed lab URL
                if 200 <= response.status < 500:
                    return
        except Exception as exc:  # pragma: no cover - integration retry loop
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def _count(result: dict) -> int:
    assert result.get("ok") is True, result
    value = result.get("data", {}).get("count", 0)
    return int(value)


def main() -> int:
    target = os.environ.get("JUICE_SHOP_URL", "http://127.0.0.1:3000").rstrip("/")
    burp_api = os.environ.get("BURP_API_URL", "http://127.0.0.1:8111").rstrip("/")

    # Make the Munin wrapper target the real extension started by the workflow.
    os.environ.setdefault("BURP_API_HOST", "127.0.0.1")
    os.environ.setdefault("BURP_API_PORT", "8111")
    os.environ.setdefault("BURP_API_TIMEOUT", "20")

    print(f"waiting for Juice Shop at {target}")
    _wait_http(target + "/")
    print(f"waiting for Valravn Burp API at {burp_api}")
    _wait_http(burp_api + "/api/health")

    # Force interception off via the live Montoya API, then explicitly
    # constrain active lab traffic to Juice Shop. Strict mode makes an
    # accidental request outside the local target fail closed.
    with httpx.Client(base_url=burp_api, timeout=20) as client:
        intercept = client.post("/api/intercept/disable", json={})
        intercept.raise_for_status()

        response = client.post(
            "/api/scope/configure",
            json={
                "include": [target],
                "exclude": [],
                "replace": True,
                "auto_filter": False,
                "mode": "strict",
            },
        )
        response.raise_for_status()
        scope_config = response.json()
        assert scope_config.get("status") == "ok", scope_config
        assert scope_config.get("mode") == "strict", scope_config

    from munin.mcp.tools.burp_tool import (  # imported after env is fixed
        burp_check_scope,
        burp_get_proxy_count,
        burp_health_check,
        burp_invoke,
        burp_status,
    )

    health = burp_health_check()
    assert health.get("ok") is True, health
    assert health.get("data", {}).get("healthy") is True, health

    status = burp_status(probe=True)
    assert status.get("ok") is True, status
    assert status.get("data", {}).get("status") == "ok", status

    scoped = burp_check_scope(target + "/")
    assert scoped.get("ok") is True, scoped
    assert scoped.get("data", {}).get("in_scope") is True, scoped

    before = _count(burp_get_proxy_count(host="127.0.0.1"))

    marker = f"munin-valravn-juice-{int(time.time())}"
    sent = burp_invoke(
        endpoint="/api/http/send",
        method="POST",
        json_body={
            "method": "GET",
            "url": target + "/",
            "headers": {"X-Valravn-Lab": marker},
        },
    )
    assert sent.get("ok") is True, sent
    data = sent.get("data", {})
    assert int(data.get("status_code", 0)) == 200, sent

    # This is the critical assertion: a non-negative history index proves the
    # extension used Burp's actual proxy listener rather than its direct-send
    # fallback path.
    history_index = int(data.get("history_index", -1))
    assert history_index >= 0, sent

    after = _count(burp_get_proxy_count(host="127.0.0.1"))
    assert after > before, {"before": before, "after": after, "sent": sent}

    print(
        "E2E OK: Munin -> Valravn extension -> Burp Proxy -> Juice Shop; "
        f"history_index={history_index}, proxy_count={before}->{after}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Burp/Juice Shop E2E failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
