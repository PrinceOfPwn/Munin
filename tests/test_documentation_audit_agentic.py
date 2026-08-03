# tags: [tests, documentation-audit, langgraph, bounded-context]
"""Tests for the bounded agentic documentation-audit wrapper."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import documentation_audit as core  # noqa: E402
import documentation_audit_agentic as agentic  # noqa: E402


class FakeModel:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.provider_name = "fake:model"

    def tokenize(self, text: bytes, add_bos: bool = True) -> list[int]:
        del add_bos
        return list(range(max(1, len(text) // 8)))

    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        payload = self.responses.pop(0)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(payload),
                    }
                }
            ]
        }


class FakeIndex:
    def __init__(self, candidates: list[str]):
        self.candidates = candidates

    def candidates_for(
        self,
        path: Path,
        content: str,
        limit: int,
    ) -> list[str]:
        del path, content
        return self.candidates[:limit]


def runtime_config() -> agentic.RuntimeConfig:
    audit = core.AuditConfig()
    audit.model.context_tokens = 32_768
    audit.model.file_output_tokens = 512
    audit.scan.minimum_confidence = 0.0
    return agentic.RuntimeConfig(
        audit=audit,
        agent=agentic.AgentConfig(
            enabled=True,
            max_context_files=2,
            max_candidates=10,
            planning_output_tokens=128,
            context_token_budget=10_000,
        ),
        github_models=agentic.GitHubModelsConfig(enabled=False),
    )


def valid_report(path: str) -> dict[str, Any]:
    return {
        "file": path,
        "language": "python",
        "summary": "No documentation problems were found.",
        "findings": [],
    }


def test_python_import_resolution_is_local_and_allowlisted(tmp_path: Path) -> None:
    primary = tmp_path / "pkg" / "service.py"
    dependency = tmp_path / "pkg" / "helpers.py"
    primary.parent.mkdir(parents=True)
    primary.write_text("from .helpers import public_name\n", encoding="utf-8")
    dependency.write_text("def public_name():\n    return 1\n", encoding="utf-8")

    relative_primary = Path("pkg/service.py")
    index = object.__new__(agentic.RepositoryIndex)
    index.scan_config = core.ScanConfig()
    index.files = {
        "pkg/service.py": relative_primary,
        "pkg/helpers.py": Path("pkg/helpers.py"),
    }

    assert index._python_candidates(
        relative_primary,
        primary.read_text(encoding="utf-8"),
    ) == ["pkg/helpers.py"]


def test_graph_can_decline_related_context(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    primary = Path("primary.py")
    primary.write_text("from dependency import external\n", encoding="utf-8")
    Path("dependency.py").write_text("def external():\n    return 1\n", encoding="utf-8")

    model = FakeModel(
        [
            {
                "needs_context": False,
                "requested_files": [],
                "reason": "The local usage is enough.",
            },
            valid_report("primary.py"),
        ]
    )
    graph = agentic.build_review_graph(
        model,
        FakeIndex(["dependency.py"]),
        runtime_config(),
    )

    report = agentic.analyze_file_agentically(graph, primary)

    assert report["metadata"]["context_loaded"] == []
    assert report["metadata"]["context_requested"] == []
    assert not model.responses


def test_graph_reads_only_selected_allowlisted_complete_files(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.chdir(tmp_path)
    primary = Path("primary.py")
    primary.write_text("from dependency import external\n", encoding="utf-8")
    dependency_content = "def external(value: int) -> int:\n    return value + 1\n"
    Path("dependency.py").write_text(dependency_content, encoding="utf-8")
    Path("secret.py").write_text("SECRET = 'do not read'\n", encoding="utf-8")

    model = FakeModel(
        [
            {
                "needs_context": True,
                "requested_files": ["secret.py", "dependency.py", "../outside.py"],
                "reason": "The imported contract is needed.",
            },
            valid_report("primary.py"),
        ]
    )
    graph = agentic.build_review_graph(
        model,
        FakeIndex(["dependency.py"]),
        runtime_config(),
    )

    report = agentic.analyze_file_agentically(graph, primary)

    assert report["metadata"]["context_requested"] == ["dependency.py"]
    assert report["metadata"]["context_loaded"] == ["dependency.py"]
    assert report["metadata"]["context_skipped"] == []
    assert not model.responses


def test_preferred_remote_model_order_follows_configured_ranking() -> None:
    available = [
        "publisher/qwen3.6-27b",
        "publisher/kimi-k3",
        "publisher/claude-opus-5",
    ]

    assert (
        agentic._select_preferred_model(
            available,
            ("claude-opus-5", "kimi-k3", "qwen3.6-27b"),
        )
        == "publisher/claude-opus-5"
    )


def test_model_hash_verification_rejects_unexpected_weights(tmp_path: Path) -> None:
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"not-the-pinned-model")
    config = runtime_config()
    config.agent.model_sha256 = "0" * 64
    router = agentic.ModelRouter(config, tmp_path)

    try:
        router._verify_model_hash(model_file)
    except RuntimeError as exc:
        assert "SHA256 mismatch" in str(exc)
    else:
        raise AssertionError("unexpected GGUF hash was accepted")
