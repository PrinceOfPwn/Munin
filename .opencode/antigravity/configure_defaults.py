# tags: [antigravity, setup, opencode, bootstrap, settings]
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / ".opencode" / "antigravity" / "default-settings.json"
GEMINI_DIR = Path.home() / ".gemini"
TARGET = GEMINI_DIR / "antigravity-cli" / "settings.json"
TRUSTED_FOLDERS = GEMINI_DIR / "trustedFolders.json"
PROJECTS = GEMINI_DIR / "projects.json"


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


def workspace_variants(path: Path) -> list[str]:
    """Return the absolute path and, on Windows, both backslash/slash spellings.

    On Windows, Antigravity normalizes paths before rule evaluation by
    stripping the drive letter and converting backslashes to forward slashes,
    but trust stores and rule matchers handle both spellings. Registering both
    makes the trust entry robust to whichever spelling agy ends up comparing
    against.
    """
    s = str(path.resolve())
    variants = [s]
    # Windows-style path → forward-slash variant
    if "\\" in s:
        variants.append(s.replace("\\", "/"))
    return variants


def ensure_trust(repo_path: Path) -> dict[str, dict[str, Any]]:
    """Register the repository as a trusted workspace across the three files
    agy consults on Windows and Unix. Idempotent.

    Returns a short report of what was added.
    """
    report: dict[str, dict[str, Any]] = {
        "trustedWorkspaces": {"before": [], "added": []},
        "trustedFolders": {"before": [], "added": []},
        "projects": {"before": [], "added": []},
    }
    repo_str = str(repo_path.resolve())
    repo_fwd = repo_str.replace("\\", "/")
    repo_lower = repo_str.lower()
    repo_name = repo_path.resolve().name

    # --- ~/.gemini/antigravity-cli/settings.json.trustedWorkspaces ---
    report["trustedWorkspaces"]["before"] = list(
        load_json(TARGET).get("trustedWorkspaces", []) if TARGET.exists() else []
    )
    trust_list: list[str] = list(report["trustedWorkspaces"]["before"])
    for variant in workspace_variants(repo_path):
        if variant not in trust_list:
            trust_list.append(variant)
            report["trustedWorkspaces"]["added"].append(variant)

    # --- ~/.gemini/trustedFolders.json ---
    folders: dict[str, str] = {}
    if TRUSTED_FOLDERS.exists():
        loaded = load_json(TRUSTED_FOLDERS)
        if isinstance(loaded, dict):
            folders = dict(loaded)
    report["trustedFolders"]["before"] = list(folders.keys())
    for variant in workspace_variants(repo_path):
        if variant not in folders:
            folders[variant] = "TRUST_FOLDER"
            report["trustedFolders"]["added"].append(variant)
    # Cross-platform-safe: also key by the lowercase / fwd-slash variant so
    # the trust matches regardless of how agy normalized the cwd.
    if repo_fwd.lower() not in folders:
        folders[repo_fwd.lower()] = "TRUST_FOLDER"
        if repo_fwd.lower() not in report["trustedFolders"]["added"]:
            report["trustedFolders"]["added"].append(repo_fwd.lower())

    # --- ~/.gemini/projects.json ---
    projects_doc: dict[str, Any] = {}
    if PROJECTS.exists():
        loaded = load_json(PROJECTS)
        if isinstance(loaded, dict):
            projects_doc = dict(loaded)
    projects_map: dict[str, str] = dict(projects_doc.get("projects", {}))
    report["projects"]["before"] = list(projects_map.keys())
    # key by several spellings agy has emitted on Windows.
    for key in {repo_lower, repo_fwd.lower(), repo_lower.replace("\\", "/")}:
        if key and key not in projects_map:
            projects_map[key] = repo_name
            report["projects"]["added"].append(key)
    projects_doc["projects"] = projects_map

    return {
        "trust_list": trust_list,
        "folders": folders,
        "projects_doc": projects_doc,
        "report": report,
    }


def grant_scoped_file_perms(permissions: dict[str, Any], repo_path: Path) -> list[str]:
    """Add scoped write_file / read_file allow rules for the repository, in
    both backslash and forward-slash spellings (agy normalizes on Windows but
    both spellings were observed being matched against). Idempotent."""
    repo_str = str(repo_path.resolve())
    repo_fwd = repo_str.replace("\\", "/")
    to_add = []
    for variant in (repo_str, repo_fwd, repo_lower := repo_str.lower(),
                    repo_fwd.lower()):
        for action in ("write_file", "read_file"):
            rule = f"{action}({variant})"
            to_add.append(rule)
    allow: list[Any] = list(permissions.get("allow", []))
    added: list[str] = []
    for rule in to_add:
        if rule not in allow:
            allow.append(rule)
            added.append(rule)
    permissions["allow"] = allow
    return added


def main() -> None:
    defaults = load_json(TEMPLATE)
    existing: dict[str, Any] = {}

    if TARGET.exists():
        existing = load_json(TARGET)

    # Step A — merge the static defaults (artifactReviewPolicy, permissions,
    # etc.).
    merged = deep_merge(existing, defaults)

    # Step B — register THIS repository as a trusted workspace and grant the
    # scoped write/read rules that headless mode requires to soft-allow file
    # edits inside the workspace.
    trust = ensure_trust(ROOT)
    merged["trustedWorkspaces"] = trust["trust_list"]
    perms = merged.setdefault("permissions", {})
    perms.setdefault("allow", [])
    perms.setdefault("deny", [])
    perms.setdefault("ask", [])
    added_rules = grant_scoped_file_perms(perms, ROOT)

    TARGET.parent.mkdir(parents=True, exist_ok=True)

    # Step C — write the trust folders + projects files alongside the settings
    # file.
    if TRUSTED_FOLDERS.exists():
        bf = TRUSTED_FOLDERS.with_suffix(".json.bak")
        bf.write_text(TRUSTED_FOLDERS.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"Backup written to {bf}")
    TRUSTED_FOLDERS.write_text(
        json.dumps(trust["folders"], indent=2) + "\n", encoding="utf-8"
    )

    if PROJECTS.exists():
        bp = PROJECTS.with_suffix(".json.bak")
        bp.write_text(PROJECTS.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"Backup written to {bp}")
    PROJECTS.write_text(
        json.dumps(trust["projects_doc"], indent=2) + "\n", encoding="utf-8"
    )

    # Step D — atomic-ish settings.json write with backup.
    if TARGET.exists():
        backup = TARGET.with_suffix(".json.bak")
        backup.write_text(TARGET.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"Backup written to {backup}")

    TARGET.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print(f"Antigravity defaults merged into {TARGET}")
    print("Existing settings were preserved; missing defaults were added.")
    print()
    print(f"Repository registered as trusted workspace: {ROOT}")
    rep = trust["report"]
    print(f"  settings.trustedWorkspaces: added {len(rep['trustedWorkspaces']['added'])} entries")
    print(f"  trustedFolders.json:        added {len(rep['trustedFolders']['added'])} entries")
    print(f"  projects.json:              added {len(rep['projects']['added'])} entries")
    print(f"  scoped permission allow-rules added: {len(added_rules)}")
    print()
    print("Probe headless write with the antigravity-setup skill before")
    print("running a real delegation via antigravity_delegate.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover — installer-side error path
        print(f"configure_defaults.py: {exc}", file=sys.stderr)
        raise
