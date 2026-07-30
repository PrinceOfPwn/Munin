"""In-process Python sandbox used by `tool_forge`.

**Security disclosure (read `docs/security-notes.md`):** this is NOT a real sandbox.
`langgraph-codeact` executes the LLM's Python via `exec()` in the current process.
The mitigations below (AST guard, restricted builtins, timeout, jailed workspace) raise
the bar against accidental damage but a determined adversarial LLM CAN escape them
(bytecode tricks, `__class__.__subclasses__()`, monkey-patching, etc.).

Threat model
------------
- Trusted LLM (operator's own NIM/OpenAI/Groq key).
- Trusted spec author (operator or Munin core agent).
- Untrusted network content that flows through (Tavily/Hugin results, LDAP data).

If any of those become untrusted, replace this sandbox with docker-in-docker
(see `docs/security-notes.md` §alternatives).
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import io
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("munin.sandbox")


# Modules never allowed. Even inside an allow-list, these are dropped.
_HARD_BANNED_MODULES: frozenset[str] = frozenset(
    {
        "subprocess",
        "ctypes",
        "os.system",
        "posix",
        "nt",
        "socketserver",
        "multiprocessing",
    }
)

# Attribute access we reject at AST level.
# Includes shell/exec/fork/network primitives commonly reached via `import os`
# even when os itself is on an allowlist (a tool that legitimately calls
# os.path.join should never need os.system). Adds shutil.rmtree so a "cleanup"
# tool can't nuke the workspace.
_BANNED_ATTRS: frozenset[str] = frozenset(
    {
        # Reflection escape routes
        "__class__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        "mro",
        "gi_frame",
        "cr_frame",
        "gi_code",
        "cr_code",
        "f_locals",
        "f_globals",
        # Process / shell execution
        "system",
        "popen",
        "spawn",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "fork",
        "forkpty",
        "_exit",
        "kill",
        "killpg",
        "setuid",
        "setgid",
        "seteuid",
        "setegid",
        "startfile",
        # Dangerous fs
        "rmtree",
        "chmod",
        "chown",
        "lchown",
        "unlink",
        # Dynamic code
        "get_data",
        "exec_module",
        "run_module",
        "load_module",
    }
)

# Free-form builtins the code is allowed to touch. Any name outside this set is
# resolved to None (unavailable). exec/eval/compile/__import__ are deliberately absent.
_SAFE_BUILTIN_NAMES: frozenset[str] = frozenset(
    {
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable",
        "chr", "complex", "dict", "divmod", "enumerate", "filter", "float", "format",
        "frozenset", "hash", "hex", "id", "int", "isinstance", "issubclass", "iter",
        "len", "list", "map", "max", "min", "next", "object", "oct", "ord", "pow",
        "print", "range", "repr", "reversed", "round", "set", "slice", "sorted", "str",
        "sum", "tuple", "type", "zip", "True", "False", "None", "NotImplemented",
        "Ellipsis", "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "AttributeError", "RuntimeError", "StopIteration", "ZeroDivisionError",
        "ArithmeticError", "OverflowError", "IOError", "OSError", "FileNotFoundError",
        "LookupError", "NameError", "UnicodeError", "UnicodeDecodeError",
    }
)


class SandboxViolation(RuntimeError):
    """Raised when the AST guard rejects code before execution."""


@dataclass
class SandboxResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    return_value: Any = None
    duration_seconds: float = 0.0
    log: list[str] = field(default_factory=list)


def _validate_ast(tree: ast.Module, allowed_imports: set[str]) -> list[str]:
    """AST walk: enforce import allowlist + reject dangerous attribute access."""
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _HARD_BANNED_MODULES or alias.name in _HARD_BANNED_MODULES:
                    raise SandboxViolation(f"import '{alias.name}' is hard-banned")
                if allowed_imports and root not in allowed_imports:
                    raise SandboxViolation(f"import '{alias.name}' not in allowlist {sorted(allowed_imports)}")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            if module in _HARD_BANNED_MODULES or (node.module or "") in _HARD_BANNED_MODULES:
                raise SandboxViolation(f"from-import '{node.module}' is hard-banned")
            if allowed_imports and module not in allowed_imports:
                raise SandboxViolation(f"from-import '{node.module}' not in allowlist {sorted(allowed_imports)}")
        elif isinstance(node, ast.Attribute):
            if node.attr in _BANNED_ATTRS:
                raise SandboxViolation(f"attribute access '{node.attr}' is banned")
        elif isinstance(node, ast.Name):
            if node.id in {"exec", "eval", "compile", "__import__", "__builtins__", "globals", "locals", "vars"}:
                raise SandboxViolation(f"name '{node.id}' is banned")
    return findings


def _restricted_builtins(allowed_imports: set[str]) -> dict[str, Any]:
    safe = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES if hasattr(builtins, name)}

    # Provide a restricted __import__ that honors the allowlist.
    real_import = builtins.__import__

    def _guarded_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        root = name.split(".", 1)[0]
        if root in _HARD_BANNED_MODULES or name in _HARD_BANNED_MODULES:
            raise SandboxViolation(f"runtime import '{name}' is hard-banned")
        if allowed_imports and root not in allowed_imports:
            raise SandboxViolation(f"runtime import '{name}' not in allowlist {sorted(allowed_imports)}")
        return real_import(name, globals, locals, fromlist, level)

    safe["__import__"] = _guarded_import
    return safe


def run_code(
    code: str,
    *,
    allowed_imports: set[str] | None = None,
    timeout_seconds: int = 20,
    workspace_dir: Path | None = None,
) -> SandboxResult:
    """Execute ``code`` in a restricted namespace with the given constraints.

    ``allowed_imports`` — set of top-level module names allowed (e.g. {"ldap3", "json"}).
    Empty set means NO imports allowed. ``None`` means no allowlist check (still hard-banned
    modules are rejected).
    """
    log: list[str] = []
    log.append(f"allowed_imports={sorted(allowed_imports) if allowed_imports is not None else '<any-non-banned>'}")
    log.append(f"timeout={timeout_seconds}s")

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return SandboxResult(ok=False, error=f"SyntaxError: {exc}", log=log)

    guard_set = allowed_imports if allowed_imports is not None else set()
    try:
        _validate_ast(tree, guard_set)
    except SandboxViolation as exc:
        return SandboxResult(ok=False, error=f"SandboxViolation: {exc}", log=log)

    # Jail cwd
    tmp_root = workspace_dir or Path(tempfile.mkdtemp(prefix="munin_forge_"))
    original_cwd = os.getcwd()
    os.chdir(str(tmp_root))
    log.append(f"cwd={tmp_root}")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    env: dict[str, Any] = {"__builtins__": _restricted_builtins(guard_set)}
    result_holder: dict[str, Any] = {"return": None, "error": None}

    def _target() -> None:
        try:
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                exec(compile(tree, filename="<munin-sandbox>", mode="exec"), env, env)
                # Expose a conventional `result` variable if the script assigned one.
                if "result" in env:
                    result_holder["return"] = env["result"]
        except SandboxViolation as exc:
            result_holder["error"] = f"SandboxViolation: {exc}"
        except Exception as exc:  # noqa: BLE001 — any runtime error is capture-worthy
            result_holder["error"] = f"{type(exc).__name__}: {exc}"

    started = _monotonic()
    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    elapsed = _monotonic() - started
    os.chdir(original_cwd)

    if thread.is_alive():
        log.append(f"TIMEOUT after {timeout_seconds}s — thread abandoned (Python cannot force-kill)")
        return SandboxResult(
            ok=False,
            error=f"timeout after {timeout_seconds}s",
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            duration_seconds=elapsed,
            log=log,
        )

    if result_holder["error"]:
        return SandboxResult(
            ok=False,
            error=result_holder["error"],
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            duration_seconds=elapsed,
            log=log,
        )

    return SandboxResult(
        ok=True,
        stdout=stdout_buffer.getvalue(),
        stderr=stderr_buffer.getvalue(),
        return_value=result_holder["return"],
        duration_seconds=elapsed,
        log=log,
    )


def _monotonic() -> float:
    import time

    return time.monotonic()


def validate_source_file(script_path: Path, allowed_imports: set[str] | None = None) -> None:
    """Re-run the AST guard on an on-disk script BEFORE importing it.

    Called from ``registry.register_state_only`` — a runner subprocess persists a
    forged tool, and later the MCP server re-imports it during rehydrate. Both
    contexts must reject scripts that have been tampered with post-forge or that
    somehow slipped through the initial AST walk. Raises SandboxViolation if
    the file contains banned imports or attributes.

    ``allowed_imports=None`` means "just enforce hard-banned + attribute rules";
    pass the same allowlist used at forge time for stricter checking.
    """
    source = Path(script_path).read_text(encoding="utf-8")
    tree = ast.parse(source, mode="exec")
    _validate_ast(tree, allowed_imports if allowed_imports is not None else set())
