"""Tool-forge subagent — writes and validates new Python tools iteratively.

Strategy (deliberately explicit — no LangGraph magic here for full control):

1. Ask the LLM for a single-function Python script matching the spec (with a strict
   response schema: description, function_name, allowed_imports, script).
2. Extract the code block.
3. Run the AST guard + restricted-exec sandbox with the declared allowed_imports.
4. If OK: persist to ``munin/generated/<slug>.py`` and return metadata for
   `registry.register` to attach the tool live to the MCP server.
5. If FAIL: feed the error back to the LLM and iterate (up to ``max_iterations``).
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from ..core.llm_client import LLMClient
from ..mcp.config import safe_slug
from ..mcp.registry import signature_to_json_schema
from ..mcp.shared_state import SharedStateStore
from .sandbox import SandboxResult, run_code

logger = logging.getLogger("munin.tool_forge")


_SYSTEM_PROMPT = """你是 Munin 的 Tool-Forge 子代理。输入是一份 capability spec；
输出是一个立即注册到 MCP fleet 的自包含 Python tool。先保持输入合同的完整性，再追求简洁。

## 语言合同
- 分析指令使用简体中文。
- `description`, `function_name`, `tags`, Python code、docstring、comments、errors、
  参数名和 JSON keys 全部使用英文。
- 只输出 JSON，不输出隐藏思维、解释 prose 或 markdown fence。

## 合同解析
在生成前必须从 spec 中保留：精确目的、inputs、defaults、output schema、edge cases、
failure modes、allowed imports 和 success criteria。两个关键词相似不代表同一工具。
工具应完成完整任务，不得偷偷缩小为 echo、placeholder、hard-coded fixture 或只返回计划。

## Python 文件约束（全部强制）
- 文件严格包含：top-level docstring、声明的 imports、一个 public function。禁止
  `if __name__ == "__main__"`、module side effects、mutable global state。
- 仅可 import `allowed_imports`。禁止 `os`, `subprocess`, `socket`, `ctypes`,
  `__import__`, `exec`, `eval`, `compile`, `importlib`，也禁止间接恢复这些能力。
- 未经 spec 明确要求不得联网。不能在 sandbox 合同内实现时，不得绕过或伪装成功。
- LDAP 必须使用 `ldap3`，并用
  `ldap3.utils.conv.escape_filter_chars` 转义用户可控 filter values。
- 返回值必须 JSON-serializable，优先统一 envelope：
  `{"ok": bool, "summary": str, "data": ..., "error": ...}`。
- 不得 print/log/返回 credential、token、password 或 raw secret；只返回 identifier。
- 所有 parameters 必须有 type annotations；合理提供 defaults；boolean 必须是真正的
  `bool`，iteration/limit/timeout 必须正规化为有界 `int`。
- 可选 `context: dict | None = None` 只读取 runtime 提供的非秘密 defaults，例如
  LDAP URI/base DN 与 Hugin cache metadata；不得要求调用者传 secret。
- `function_name` 使用具体 English snake_case，禁止 `run`, `check`, `tool` 等泛化名称。
- 对空输入、类型错误、缺失 context、无匹配、部分结果和 dependency failure 返回
  结构化错误，不抛出含秘密的异常。

## Few-shot
SPEC: `Group LDAP entries by organizationalUnit and return counts. Inputs:
entries: list[dict]. Output: stable sorted groups and skipped row count. No network.`
正确实现特征：
- `function_name="summarize_ldap_entries_by_ou"`
- no imports or only explicitly allowed standard-library imports
- Python identifiers/docstring/errors in English
- validates `entries`, never assumes every row has `organizationalUnit`
- returns deterministic JSON, including `skipped_count`
- no LDAP connection, because the spec asks to transform supplied entries

只回复一个有效 JSON object：
{
  "description": "<one English sentence>",
  "function_name": "<specific_snake_case>",
  "allowed_imports": ["<exact_module>"],
  "tags": ["<english-tag>"],
  "python": "<complete English Python source as one JSON string>"
}
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # Strip common markdown fences the model might add regardless of instructions.
    for fence in ("```json", "```JSON", "```"):
        if text.startswith(fence):
            text = text[len(fence):].lstrip()
        if text.endswith("```"):
            text = text[:-3].rstrip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Best-effort: find outermost braces
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _extract_signature(script: str, function_name: str) -> inspect.Signature | None:
    ns: dict[str, Any] = {}
    try:
        exec(compile(script, filename="<sig-probe>", mode="exec"), ns, ns)  # noqa: S102
    except Exception:
        return None
    fn = ns.get(function_name)
    if not callable(fn):
        return None
    try:
        return inspect.signature(fn)
    except (TypeError, ValueError):
        return None


class ToolForgeSubagent:
    def __init__(
        self,
        state: SharedStateStore,
        *,
        allowed_imports: list[str] | None = None,
        max_iterations: int = 5,
        llm: LLMClient | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.state = state
        self.allowed_imports_hint = allowed_imports or []
        self.max_iterations = max_iterations
        self.llm = llm or LLMClient(state.settings)
        self.on_progress = on_progress

    def _emit(self, stage: str, message: str, **details: Any) -> None:
        """Record and expose a lifecycle milestone without model reasoning."""
        event = {"stage": stage, "message": message, **details}
        try:
            self.state.episodic_record(
                agent="tool_forge",
                action=stage,
                output_data=event,
                tags=["forge", "lifecycle"],
            )
        except Exception:
            logger.debug("could not record forge progress", exc_info=True)
        if self.on_progress is not None:
            try:
                self.on_progress(event)
            except Exception:
                logger.debug("could not publish forge progress", exc_info=True)

    def forge(self, spec: str) -> dict[str, Any]:
        transcript: list[str] = [f"spec: {spec}"]
        error_hint = ""
        for iteration in range(1, self.max_iterations + 1):
            transcript.append(f"--- iteration {iteration} ---")
            self._emit(
                "forge_generation",
                f"Requesting implementation draft ({iteration}/{self.max_iterations})",
                forge_iteration=iteration,
                max_iterations=self.max_iterations,
            )
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self._render_user_prompt(spec, error_hint),
                },
            ]
            try:
                completion = self.llm.chat(messages=messages, temperature=0.1)
            except Exception as exc:
                return {"ok": False, "summary": "LLM call failed", "error": {"code": "llm_failed", "message": str(exc)}, "log_summary": "\n".join(transcript)}
            content = completion["choices"][0]["message"]["content"] or ""
            data = _extract_json(content)
            if not data:
                self._emit("forge_validation", "Model reply was not valid forge JSON", forge_iteration=iteration, ok=False)
                error_hint = "Your last response was not valid JSON. Return ONLY the JSON object as specified."
                transcript.append("bad JSON")
                continue
            script = str(data.get("python", ""))
            function_name = str(data.get("function_name", "")).strip()
            allowed = set(data.get("allowed_imports", [])) | set(self.allowed_imports_hint)
            if not script or not function_name:
                self._emit("forge_validation", "Generated draft is missing required fields", forge_iteration=iteration, ok=False)
                error_hint = "Missing `python` or `function_name` in your JSON."
                transcript.append("missing fields")
                continue

            self._emit("forge_validation", "Validating draft in the sandbox", forge_iteration=iteration)
            sandbox_result = self._exercise(script, function_name, allowed)
            transcript.append(f"sandbox ok={sandbox_result.ok} error={sandbox_result.error}")
            if not sandbox_result.ok:
                self._emit("forge_validation", "Sandbox rejected generated draft", forge_iteration=iteration, ok=False)
                error_hint = (
                    f"Your previous attempt failed sandbox validation:\n"
                    f"error: {sandbox_result.error}\n"
                    f"stderr: {sandbox_result.stderr[:400]}\n"
                    f"Adjust the code accordingly, keeping the same JSON response schema."
                )
                continue

            # Persist
            slug = safe_slug([function_name])
            script_path = self.state.settings.generated_tools_dir / f"{slug}.py"
            self._emit("forge_persist", "Persisting validated Python source", forge_iteration=iteration, tool_slug=slug)
            script_path.write_text(script, encoding="utf-8")
            sig = _extract_signature(script, function_name)
            signature_json = signature_to_json_schema(sig) if sig else {}
            signature_json["function_name"] = function_name
            self.log_success(spec=spec, slug=slug, description=data.get("description", ""), tags=data.get("tags", []))
            self._emit("forge_ready", "Validated Python source is ready for MCP registration", forge_iteration=iteration, tool_slug=slug, ok=True)
            return {
                "ok": True,
                "summary": f"forged {slug} in {iteration} iterations",
                "slug": slug,
                "function_name": function_name,
                "description": data.get("description", ""),
                "tags": data.get("tags", []),
                "script_path": str(script_path),
                "signature": signature_json,
                "iterations": iteration,
                "log_summary": "\n".join(transcript),
            }

        return {
            "ok": False,
            "summary": f"exhausted {self.max_iterations} iterations without a valid tool",
            "error": {"code": "forge_exhausted", "message": error_hint or "max iterations reached"},
            "log_summary": "\n".join(transcript),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _render_user_prompt(self, spec: str, error_hint: str) -> str:
        pieces = [f"SPEC: {spec}"]
        if self.allowed_imports_hint:
            pieces.append("ALLOWED IMPORTS (hard-required in your response): " + ", ".join(self.allowed_imports_hint))
        if error_hint:
            pieces.append("PREVIOUS FEEDBACK:\n" + error_hint)
        pieces.append(
            "Remember: single function, JSON reply only, no markdown, no side effects at import."
        )
        return "\n\n".join(pieces)

    def _exercise(self, script: str, function_name: str, allowed_imports: set[str]) -> SandboxResult:
        """Static + smoke-test: parse the code, ensure the function is defined and calls."""
        # Only import-time check; we don't invoke the function (arguments unknown).
        probe = script + (
            f"\nresult = {{'defined': callable({function_name})}}\n"
        )
        return run_code(probe, allowed_imports=allowed_imports, timeout_seconds=15)

    def log_success(self, *, spec: str, slug: str, description: str, tags: list[str]) -> None:
        self.state.episodic_record(
            agent="tool_forge",
            action="forge_success",
            input_data={"spec": spec},
            output_data={"slug": slug, "description": description, "tags": tags},
            tags=["forge", *tags],
        )
