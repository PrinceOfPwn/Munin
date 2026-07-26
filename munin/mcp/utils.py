from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n...[truncated {len(value) - limit} chars]"


def stderr_tail(value: str, lines: int = 20) -> str:
    chunks = value.strip().splitlines()
    return "\n".join(chunks[-lines:])


def shell_join(parts: Iterable[Any]) -> str:
    """
    Portable shell join. POSIX → shlex.quote. Windows → subprocess.list2cmdline.
    Filters out None and empty strings so no bare '' tokens end up in the command.
    """
    clean = [str(part) for part in parts if part is not None and str(part) != ""]
    if os.name == "nt":
        return subprocess.list2cmdline(clean)
    return " ".join(shlex.quote(part) for part in clean)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=False)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def first_non_empty(*values: str) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def env_assignments(env: dict[str, str] | None) -> str:
    """
    Inline env assignments prefix for POSIX shells (`KEY=val KEY2=val2 cmd`).
    On Windows cmd.exe this format isn't supported inline; caller must wrap with `set X=Y &&` or
    use subprocess env= parameter. We keep POSIX behavior as best-effort.
    """
    if not env:
        return ""
    return " ".join(f"{key}={shlex.quote(str(value))}" for key, value in env.items())


def count_existing_files(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


def parse_targets(raw: str) -> list[str]:
    if not raw.strip():
        return []
    values: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        item = line.strip()
        if item:
            values.append(item)
    return values


def split_extra_args(raw: str) -> list[str]:
    """
    Split user-supplied additional_args using shlex so multi-token strings
    like `-Pn --top-ports 100` become ['-Pn', '--top-ports', '100'] instead of
    collapsing into a single quoted token when passed to shell_join.
    """
    if not raw or not raw.strip():
        return []
    return shlex.split(raw)


def bool_icon(value: bool) -> str:
    return "yes" if value else "no"
