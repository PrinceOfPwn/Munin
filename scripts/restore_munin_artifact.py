#!/usr/bin/env python3
"""Restore Munin's latest trusted durable-state GitHub Actions artifact.

This first-party helper deliberately uses only Python's standard library. It
selects an artifact created by the same repository, workflow, branch and an
allowed actor, verifies GitHub's SHA-256 digest when present, and extracts only
Munin's durable-state paths without using ``ZipFile.extractall``.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

API_VERSION = "2022-11-28"
DEFAULT_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_FILES = 10_000
CHUNK_SIZE = 1024 * 1024

_ALLOWED_EXACT = frozenset(
    {
        "data/shared_state.sqlite",
        "data/shared_state.sqlite-wal",
        "data/shared_state.sqlite-shm",
    }
)
_ALLOWED_DIRECTORY_ROOTS = frozenset({"data/soul_pending", "data/wake_artifacts", "munin/generated"})
_ALLOWED_PREFIXES = (
    "data/soul_pending/",
    "data/wake_artifacts/",
    "munin/generated/",
)


class RestoreError(RuntimeError):
    """Raised when an artifact cannot be trusted or restored safely."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return GitHub's signed redirect without forwarding the bearer token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True)
class SelectedArtifact:
    artifact_id: int
    run_id: int
    name: str
    size_in_bytes: int
    digest: str | None
    created_at: str
    actor: str
    branch: str


class GitHubApi:
    def __init__(self, *, repository: str, token: str, api_url: str = "https://api.github.com") -> None:
        if repository.count("/") != 1:
            raise RestoreError("repository must use OWNER/REPO format")
        if not token:
            raise RestoreError("GH_TOKEN or GITHUB_TOKEN is required")
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "munin-state-restore",
        }

    def json(self, path: str) -> Mapping[str, Any]:
        request = urllib.request.Request(self.api_url + path, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RestoreError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RestoreError(f"GitHub API request failed: {exc.reason}") from exc
        if not isinstance(payload, Mapping):
            raise RestoreError("GitHub API returned a non-object response")
        return payload

    def signed_artifact_url(self, artifact_id: int) -> str:
        path = f"/repos/{self.repository}/actions/artifacts/{artifact_id}/zip"
        request = urllib.request.Request(self.api_url + path, headers=self.headers)
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            opener.open(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 302:
                location = exc.headers.get("Location", "")
                if not location.startswith("https://"):
                    raise RestoreError("GitHub returned an invalid artifact redirect URL")
                return location
            if exc.code == 410:
                raise RestoreError("artifact expired before it could be downloaded") from exc
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RestoreError(f"artifact download request returned HTTP {exc.code}: {detail}") from exc
        raise RestoreError("GitHub did not return the expected artifact download redirect")


def _encode_query(values: Mapping[str, str | int]) -> str:
    return urllib.parse.urlencode(values, quote_via=urllib.parse.quote)


def select_latest_artifact(
    api: GitHubApi,
    *,
    workflow: str,
    artifact_name: str,
    branch: str,
    allowed_actors: set[str],
    repository_id: int | None = None,
    max_runs: int = 100,
) -> SelectedArtifact | None:
    if not workflow or "/" in workflow or "\\" in workflow:
        raise RestoreError("workflow must be a workflow file name such as live-session.yml")
    if not branch:
        raise RestoreError("branch is required")
    if not allowed_actors:
        raise RestoreError("at least one allowed actor is required")

    workflow_id = urllib.parse.quote(workflow, safe="")
    query = _encode_query(
        {
            "branch": branch,
            "event": "workflow_dispatch",
            "status": "completed",
            "per_page": min(max_runs, 100),
        }
    )
    payload = api.json(f"/repos/{api.repository}/actions/workflows/{workflow_id}/runs?{query}")
    runs = payload.get("workflow_runs", [])
    if not isinstance(runs, list):
        raise RestoreError("GitHub returned an invalid workflow_runs collection")

    for run in runs:
        if not isinstance(run, Mapping):
            continue
        run_id = run.get("id")
        actor = run.get("actor")
        head_repository = run.get("head_repository")
        if (
            not isinstance(run_id, int)
            or not isinstance(actor, Mapping)
            or not isinstance(head_repository, Mapping)
        ):
            continue
        actor_login = actor.get("login")
        if actor_login not in allowed_actors:
            continue
        if run.get("event") != "workflow_dispatch" or run.get("status") != "completed":
            continue
        if run.get("head_branch") != branch:
            continue
        if head_repository.get("full_name") != api.repository:
            continue
        head_repository_id = head_repository.get("id")
        if repository_id is not None and head_repository_id != repository_id:
            continue

        artifact_query = _encode_query({"name": artifact_name, "per_page": 100, "direction": "desc"})
        artifact_payload = api.json(
            f"/repos/{api.repository}/actions/runs/{run_id}/artifacts?{artifact_query}"
        )
        artifacts = artifact_payload.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise RestoreError("GitHub returned an invalid artifacts collection")

        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                continue
            workflow_run = artifact.get("workflow_run")
            if not isinstance(workflow_run, Mapping):
                continue
            if artifact.get("name") != artifact_name or artifact.get("expired") is True:
                continue
            if workflow_run.get("id") != run_id or workflow_run.get("head_branch") != branch:
                continue
            if repository_id is not None and workflow_run.get("head_repository_id") != repository_id:
                continue
            artifact_id = artifact.get("id")
            size = artifact.get("size_in_bytes")
            if not isinstance(artifact_id, int) or not isinstance(size, int) or size < 0:
                continue
            digest = artifact.get("digest")
            return SelectedArtifact(
                artifact_id=artifact_id,
                run_id=run_id,
                name=artifact_name,
                size_in_bytes=size,
                digest=digest if isinstance(digest, str) and digest else None,
                created_at=str(artifact.get("created_at") or ""),
                actor=str(actor_login),
                branch=branch,
            )
    return None


def download_artifact(
    api: GitHubApi,
    artifact: SelectedArtifact,
    *,
    destination: Path,
    max_archive_bytes: int,
) -> str:
    if artifact.size_in_bytes > max_archive_bytes:
        raise RestoreError(
            f"artifact metadata size {artifact.size_in_bytes} exceeds limit {max_archive_bytes}"
        )
    signed_url = api.signed_artifact_url(artifact.artifact_id)
    digest = hashlib.sha256()
    downloaded = 0
    request = urllib.request.Request(signed_url, headers={"User-Agent": "munin-state-restore"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, destination.open("xb") as output:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_archive_bytes:
                    raise RestoreError(f"artifact archive exceeds limit {max_archive_bytes}")
                digest.update(chunk)
                output.write(chunk)
    except urllib.error.URLError as exc:
        destination.unlink(missing_ok=True)
        raise RestoreError(f"artifact download failed: {exc.reason}") from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    actual = f"sha256:{digest.hexdigest()}"
    if artifact.digest:
        algorithm, separator, expected = artifact.digest.partition(":")
        if not separator or algorithm.lower() != "sha256" or len(expected) != 64:
            raise RestoreError(f"unsupported artifact digest format: {artifact.digest}")
        if not hmac.compare_digest(actual.lower(), artifact.digest.lower()):
            raise RestoreError("artifact SHA-256 digest does not match GitHub metadata")
    else:
        print("::warning::GitHub did not provide an artifact digest; HTTPS metadata checks still apply")
    return actual


def _normalize_member(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise RestoreError(f"unsafe ZIP member name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RestoreError(f"unsafe ZIP member path: {name!r}")
    if path.parts and ":" in path.parts[0]:
        raise RestoreError(f"drive-qualified ZIP member path: {name!r}")
    return path.as_posix().rstrip("/")


def _is_allowed(path: str) -> bool:
    return (
        path in _ALLOWED_EXACT
        or path in _ALLOWED_DIRECTORY_ROOTS
        or any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)
    )


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return bool(mode and stat.S_ISLNK(mode))


def _validate_archive(
    archive: zipfile.ZipFile,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
) -> list[tuple[zipfile.ZipInfo, str]]:
    selected: list[tuple[zipfile.ZipInfo, str]] = []
    seen: set[str] = set()
    total = 0
    file_count = 0

    for info in archive.infolist():
        normalized = _normalize_member(info.filename)
        if not _is_allowed(normalized):
            raise RestoreError(f"artifact contains a non-allowlisted path: {normalized}")
        if info.flag_bits & 0x1:
            raise RestoreError(f"encrypted ZIP member is not supported: {normalized}")
        if _is_symlink(info):
            raise RestoreError(f"symbolic links are not allowed in artifacts: {normalized}")
        if info.is_dir():
            continue
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG}:
            raise RestoreError(f"non-regular ZIP member is not allowed: {normalized}")
        if normalized in seen:
            raise RestoreError(f"duplicate ZIP member path: {normalized}")
        seen.add(normalized)
        file_count += 1
        total += info.file_size
        if file_count > max_files:
            raise RestoreError(f"artifact contains more than {max_files} files")
        if total > max_uncompressed_bytes:
            raise RestoreError(
                f"artifact expands beyond the {max_uncompressed_bytes}-byte uncompressed limit"
            )
        selected.append((info, normalized))
    return selected


def _reject_symlink_parents(destination: Path, target: Path) -> None:
    relative = target.relative_to(destination)
    current = destination
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise RestoreError(f"destination contains a symbolic-link parent: {current}")


def restore_zip(
    archive_path: Path,
    *,
    destination: Path,
    max_files: int = DEFAULT_MAX_FILES,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> list[str]:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []

    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _validate_archive(
                archive,
                max_files=max_files,
                max_uncompressed_bytes=max_uncompressed_bytes,
            )
            for index, (info, normalized) in enumerate(members):
                target = destination.joinpath(*PurePosixPath(normalized).parts)
                _reject_symlink_parents(destination, target)
                target.parent.mkdir(parents=True, exist_ok=True)
                _reject_symlink_parents(destination, target)
                if target.exists() and target.is_dir():
                    raise RestoreError(f"cannot replace directory with restored file: {normalized}")
                temporary = target.with_name(f".{target.name}.restore-{os.getpid()}-{index}.tmp")
                temporary.unlink(missing_ok=True)
                try:
                    with archive.open(info, "r") as source, temporary.open("xb") as output:
                        copied = 0
                        while True:
                            chunk = source.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > info.file_size:
                                raise RestoreError(f"ZIP member exceeded declared size: {normalized}")
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if copied != info.file_size:
                        raise RestoreError(f"ZIP member size mismatch: {normalized}")
                    os.chmod(temporary, 0o600 if normalized.startswith("data/") else 0o644)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                restored.append(normalized)
    except zipfile.BadZipFile as exc:
        raise RestoreError("artifact is not a valid ZIP archive") from exc
    return restored


def _parse_actor_values(values: Sequence[str], repository_owner: str) -> set[str]:
    actors: set[str] = set()
    for value in values:
        actors.update(item.strip() for item in value.split(",") if item.strip())
    if not actors and repository_owner:
        actors.add(repository_owner)
    return actors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--repository-id", type=int, default=int(os.getenv("GITHUB_REPOSITORY_ID", "0") or 0))
    parser.add_argument("--workflow", default="live-session.yml")
    parser.add_argument("--artifact", default="munin-state")
    parser.add_argument("--branch", default=os.getenv("GITHUB_REF_NAME", ""))
    parser.add_argument("--destination", type=Path, default=Path(os.getenv("GITHUB_WORKSPACE", ".")))
    parser.add_argument("--allowed-actor", action="append", default=[])
    parser.add_argument("--fail-if-missing", action="store_true")
    parser.add_argument("--max-archive-bytes", type=int, default=DEFAULT_MAX_ARCHIVE_BYTES)
    parser.add_argument("--max-uncompressed-bytes", type=int, default=DEFAULT_MAX_UNCOMPRESSED_BYTES)
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    owner = os.getenv("GITHUB_REPOSITORY_OWNER", args.repository.partition("/")[0])
    allowed_actors = _parse_actor_values(args.allowed_actor, owner)

    try:
        api = GitHubApi(repository=args.repository, token=token, api_url=args.api_url)
        artifact = select_latest_artifact(
            api,
            workflow=args.workflow,
            artifact_name=args.artifact,
            branch=args.branch,
            allowed_actors=allowed_actors,
            repository_id=args.repository_id or None,
        )
        if artifact is None:
            message = (
                f"no unexpired {args.artifact!r} artifact was found for workflow "
                f"{args.workflow!r}, branch {args.branch!r}, actors {sorted(allowed_actors)!r}"
            )
            if args.fail_if_missing:
                raise RestoreError(message)
            print(f"::notice::{message}")
            return 0

        if artifact.size_in_bytes > args.max_archive_bytes:
            raise RestoreError(
                f"artifact metadata size {artifact.size_in_bytes} exceeds limit {args.max_archive_bytes}"
            )
        print(
            f"Restoring {artifact.name} from run {artifact.run_id} "
            f"({artifact.created_at}, actor={artifact.actor}, branch={artifact.branch})"
        )
        with tempfile.TemporaryDirectory(prefix="munin-state-restore-") as temporary_directory:
            archive_path = Path(temporary_directory) / "artifact.zip"
            digest = download_artifact(
                api,
                artifact,
                destination=archive_path,
                max_archive_bytes=args.max_archive_bytes,
            )
            restored = restore_zip(
                archive_path,
                destination=args.destination,
                max_files=args.max_files,
                max_uncompressed_bytes=args.max_uncompressed_bytes,
            )
        print(f"Restored {len(restored)} allowlisted file(s); archive digest {digest}")
        for path in restored[:20]:
            print(f"  - {path}")
        if len(restored) > 20:
            print(f"  - … and {len(restored) - 20} more")
        return 0
    except RestoreError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
