# tags: [tests, ci-smoke, documentation-audit, qwen, langgraph]
"""Temporary CI smoke for the agentic Qwen3.5 audit path."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

IS_AUDIT_PR = os.environ.get("GITHUB_HEAD_REF") == "agent/local-documentation-audit"


@pytest.mark.skipif(not IS_AUDIT_PR, reason="temporary validation for documentation audit PR")
def test_agentic_qwen35_complete_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for command in (
        ["sudo", "apt-get", "update"],
        ["sudo", "apt-get", "install", "-y", "--no-install-recommends", "libopenblas-dev"],
    ):
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=240,
        )
        assert result.returncode == 0, result.stdout

    env = os.environ.copy()
    env["CMAKE_ARGS"] = "-DGGML_NATIVE=ON -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(os.cpu_count() or 2)
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "huggingface-hub>=0.34,<2",
            "--no-binary=llama-cpp-python",
            "llama-cpp-python==0.3.34",
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
    )
    assert install.returncode == 0, install.stdout

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import documentation_audit as core
    import documentation_audit_agentic as agentic

    runtime = agentic.load_runtime_config(Path(".munin-doc-audit.toml"))
    runtime.github_models.enabled = False
    runtime.audit.model.context_tokens = 8_192
    runtime.audit.model.file_output_tokens = 700
    runtime.audit.model.report_output_tokens = 900
    runtime.audit.model.threads = min(4, os.cpu_count() or 2)
    runtime.agent.max_context_files = 1
    runtime.agent.planning_output_tokens = 160
    runtime.agent.context_token_budget = 2_000

    monkeypatch.chdir(tmp_path)
    primary = Path("primary.py")
    dependency = Path("dependency.py")
    primary.write_text(
        '"""Utilities backed by dependency.external."""\n'
        "from dependency import external\n\n"
        "def public(value: int) -> int:\n"
        '    """Return the unchanged value."""\n'
        "    return external(value)\n",
        encoding="utf-8",
    )
    dependency.write_text(
        "def external(value: int) -> int:\n"
        '    """Increment value by one."""\n'
        "    return value + 1\n",
        encoding="utf-8",
    )

    class SmokeIndex:
        def candidates_for(self, path: Path, content: str, limit: int) -> list[str]:
            del path, content, limit
            return ["dependency.py"]

    router = agentic.ModelRouter(runtime, tmp_path / "hf-cache")
    graph = agentic.build_review_graph(router, SmokeIndex(), runtime)
    report = agentic.analyze_file_agentically(graph, primary)

    assert report["metadata"]["status"] == "analyzed", report
    assert report["metadata"]["provider"].startswith("local:"), report
    assert report["metadata"]["context_requested"] in ([], ["dependency.py"]), report
    assert report["metadata"]["context_loaded"] in ([], ["dependency.py"]), report

    statistics = core.report_statistics([report], 1)
    aggregate = core.aggregate_reports(router, [report], statistics, runtime.audit)
    assert aggregate["english_markdown"].strip()
    assert aggregate["spanish_markdown"].strip()
