from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / ".opencode" / "antigravity" / "default-settings.json"
TARGET = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def merge_unique(existing: list[Any], incoming: list[Any]) -> list[Any]:
    result = list(existing)
    for item in incoming:
        if item not in result:
            result.append(item)
    return result


def deep_merge(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing)
    for key, value in incoming.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = deep_merge(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            result[key] = merge_unique(current, value)
        elif key not in result:
            result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def main() -> None:
    defaults = load_json(TEMPLATE)
    existing: dict[str, Any] = {}

    if TARGET.exists():
        existing = load_json(TARGET)

    merged = deep_merge(existing, defaults)
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    if TARGET.exists():
        backup = TARGET.with_suffix(".json.bak")
        backup.write_text(TARGET.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"Backup written to {backup}")

    TARGET.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print(f"Antigravity defaults merged into {TARGET}")
    print("Existing settings were preserved; missing defaults were added.")


if __name__ == "__main__":
    main()
