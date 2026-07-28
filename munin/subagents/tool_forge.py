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
from pathlib import Path
from collections.abc import Callable
from typing import Any

from ..core.llm_client import LLMClient
from ..mcp.config import safe_slug
from ..mcp.shared_state import SharedStateStore
from ..mcp.registry import signature_to_json_schema
from .sandbox import SandboxResult, run_code

logger = logging.getLogger("munin.tool_forge")


_SYSTEM_PROMPT = """You are the Tool-Forge subagent of Munin.

Your job: given a natural-language spec, produce ONE self-contained Python file that
defines exactly one public function, to be exposed live as an MCP tool for the whole
agent fleet. Because it becomes immediately callable by other agents, correctness and
safety here matter more than in ordinary scratch code.

## Constraints (all mandatory)
- The file contains exactly: (a) a top-level docstring describing what the function
  does, (b) all imports, (c) one function definition. Nothing else — no
  `if __name__ == "__main__"`, no module-level side effects, no mutable module-level
  state, no network call unless the spec explicitly asks for one and the import is
  declared.
- Only import modules listed in `allowed_imports`. Never use `os`, `subprocess`,
  `socket`, `ctypes`, `__import__`, `exec`, `eval`, `compile` — and never try to reach
  them indirectly (no `getattr(builtins, ...)` chains, no `importlib`, no
  base64/marshal/pickle tricks to reconstruct a banned call). If the spec genuinely
  needs one of these, don't smuggle it in — return a description explaining what's
  missing instead of attempting a workaround.
- If the tool needs LDAP, use `ldap3` and always escape user-controlled filter
  parameters with `ldap3.utils.conv.escape_filter_chars`. Never build filter strings
  by naive f-string interpolation with user input.
- Return a JSON-serializable value (dict/list/str/int/bool). Never print, log, or
  embed a credential, token, or secret in the return value — reference it by
  identifier if the caller needs to correlate it elsewhere.
- Signature parameters must have type annotations and, where sensible, defaults, so
  the tool stays usable with partial arguments.
- New tools may optionally accept ``context: dict | None = None``. The runtime
  supplies this only when requested and it contains non-secret defaults such as
  LDAP URI/base DN and Hugin cache metadata. Never require callers to provide
  secrets as tool arguments.
- Pick a `function_name` that describes what the tool does (snake_case, specific) —
  avoid a generic name like `run` or `check` that could collide with another forged
  tool already in the catalog.

You MUST reply with a single JSON object with these keys:
{
  "description": "<one-sentence description of what the tool does>",
  "function_name": "<snake_case name>",
  "allowed_imports": ["<module1>", "<module2>", ...],
  "tags": ["<tag1>", ...],
  "python": "<full source of the .py file, as a single string>"
}
No prose outside the JSON object. No markdown fences. The JSON must be valid and
parse on the first try.
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
