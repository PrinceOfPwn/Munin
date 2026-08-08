# tags: [tests, sandboxed-html, redaction, PR-5B, sanitize_artifact_content, precheck_sandboxed_html, external-script-rejection, artifact-storage, idempotent-read]
"""PR-5B — sandboxed-html artifact pre-check in the redaction module.

Asserts:

* External ``<script src>`` imports (http, https, protocol-relative ``//host``,
  quoted and unquoted) are REJECTED with the exact ValueError contract.
* Inline ``<script>`` bodies and ``data:`` sources pass through untouched.
* External ``src`` attributes on other elements (img/iframe) are stripped at
  storage time; ``data:`` sources are preserved.
* The sanitizer is idempotent — re-running on its own output is a byte
  identical no-op, so replay reads never mutate the stored body.
* Non-sandboxed media types pass through the artifact guard untouched.
* The store call site (``ProductionStore.add_artifact`` /
  ``_insert_artifact``) enforces the pre-check on the real write path and the
  guard is NOT bypassed by ``MUNIN_REDACTION_MODE=off``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from munin.production.redaction import (
    SANDBOXED_HTML_MEDIA_TYPE,
    precheck_sandboxed_html,
    sanitize_artifact_content,
    sanitize_sandboxed_html,
)

INLINE_OK_HTML = (
    "<!doctype html><html><head></head><body>"
    "<script>document.body.dataset.ok = '1';</script>"
    '<img src="data:image/png;base64,AAAA"><p>report</p></body></html>'
)


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    store = ProductionStore.for_sqlite(tmp_path / "sandboxed_html.sqlite", master_key=b"b" * 32)
    yield store
    store.close_pools()


def _actor_and_conversation(store):
    operator = store.create_user(
        username="sandboxed-html-op", password="a strong sandboxed html password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Sandboxed HTML")
    return operator, conversation


# ---------------------------------------------------------------------------
# Pre-check: external script rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        '<script src="http://evil.test/payload.js"></script>',
        "<script src='http://evil.test/payload.js'></script>",
        '<script src="https://evil.test/payload.js"></script>',
        '<script src="//evil.test/payload.js"></script>',
        "<script src=//evil.test/payload.js></script>",
        '<script type="text/javascript" src="https://evil.test/x.js"></script>',
        '<div><script\n  src="http://evil.test/x.js"></script></div>',
        '<script src = "https://evil.test/x.js"></script>',
    ],
)
def test_rejects_external_script_src(payload: str):
    with pytest.raises(ValueError, match="External script source forbidden in sandboxed HTML artifact"):
        precheck_sandboxed_html(payload)
    with pytest.raises(ValueError, match="External script source forbidden in sandboxed HTML artifact"):
        sanitize_sandboxed_html(payload)


def test_rejects_external_script_via_dispatcher():
    with pytest.raises(ValueError, match="External script source forbidden in sandboxed HTML artifact"):
        sanitize_artifact_content(
            SANDBOXED_HTML_MEDIA_TYPE, '<script src="https://evil.test/x.js"></script>'
        )


# ---------------------------------------------------------------------------
# Sanitized pass-through
# ---------------------------------------------------------------------------


def test_inline_script_and_data_src_pass_through():
    result = sanitize_sandboxed_html(INLINE_OK_HTML)
    assert result == INLINE_OK_HTML


def test_external_img_src_is_stripped_but_data_src_survives():
    payload = (
        '<img src="http://evil.test/beacon.gif" alt="x">'
        '<img src="//evil.test/beacon2.gif" alt="y">'
        '<img src="data:image/png;base64,AAAA" alt="ok">'
        '<img src="https://evil.test/track.png">'
    )
    result = sanitize_sandboxed_html(payload)
    assert "http://evil.test/beacon.gif" not in result
    assert "//evil.test/beacon2.gif" not in result
    assert "https://evil.test/track.png" not in result
    assert 'src="data:image/png;base64,AAAA"' in result
    # src attributes are removed, not blanked — the element survives.
    assert 'alt="x"' in result


def test_external_src_on_other_elements_stripped():
    payload = '<iframe src="http://evil.test/frame" srcdoc="<p>ok</p>"></iframe>'
    result = sanitize_sandboxed_html(payload)
    assert "http://evil.test/frame" not in result
    assert 'srcdoc="<p>ok</p>"' in result


def test_non_sandboxed_media_type_passes_through_guard():
    content = '<script src="http://evil.test/x.js"></script>'
    assert sanitize_artifact_content("text/markdown", content) == content
    assert sanitize_artifact_content("", content) == content
    assert sanitize_artifact_content(None, content) == content


# ---------------------------------------------------------------------------
# Idempotent read — replay must not mutate
# ---------------------------------------------------------------------------


def test_sanitizer_is_idempotent():
    payload = (
        '<img src="http://evil.test/beacon.gif">'
        '<script>document.body.dataset.ok = "1";</script>'
        '<img src="data:image/gif;base64,R0lGOD">'
    )
    once = sanitize_sandboxed_html(payload)
    twice = sanitize_sandboxed_html(once)
    assert once == twice


def test_guard_is_not_bypassed_by_redaction_mode_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MUNIN_REDACTION_MODE", "off")
    with pytest.raises(ValueError, match="External script source forbidden in sandboxed HTML artifact"):
        sanitize_sandboxed_html('<script src="https://evil.test/x.js"></script>')


# ---------------------------------------------------------------------------
# Store integration — the write path enforces the contract
# ---------------------------------------------------------------------------


def test_add_artifact_rejects_external_script(production_store):
    operator, conversation = _actor_and_conversation(production_store)
    with pytest.raises(ValueError, match="External script source forbidden in sandboxed HTML artifact"):
        production_store.add_artifact(
            actor_id=operator["id"],
            conversation_id=conversation["id"],
            filename="report.html",
            media_type=SANDBOXED_HTML_MEDIA_TYPE,
            language="html",
            content='<script src="http://evil.test/x.js"></script>',
        )


def test_add_artifact_strips_external_img_and_stores(production_store):
    operator, conversation = _actor_and_conversation(production_store)
    artifact = production_store.add_artifact(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        filename="report.html",
        media_type=SANDBOXED_HTML_MEDIA_TYPE,
        language="html",
        content=INLINE_OK_HTML + '<img src="https://evil.test/beacon.png">',
        renderer="sandboxed-preview",
        version=1,
        provenance="PR-5B test",
        preview_url="/api/artifacts/x?inline=true",
        download_url="/api/artifacts/x?download=true",
    )
    assert artifact["media_type"] == SANDBOXED_HTML_MEDIA_TYPE
    assert artifact["renderer"] == "sandboxed-preview"

    stored = production_store.get_artifact(actor_id=operator["id"], artifact_id=artifact["id"])
    assert "https://evil.test/beacon.png" not in stored["content"]
    assert "<script>document.body.dataset.ok = '1';</script>" in stored["content"]

    # Replay read: a second read returns byte-identical content (no mutation).
    replay = production_store.get_artifact(actor_id=operator["id"], artifact_id=artifact["id"])
    assert replay["content"] == stored["content"]
    assert replay["content_hash"] == stored["content_hash"]
