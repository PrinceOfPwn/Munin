from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from scripts.restore_munin_artifact import RestoreError, _normalize_member, restore_zip


def _write_zip(path: Path, members: dict[str, bytes], *, symlink: str | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../../outside")


def test_normalize_member_rejects_traversal_and_platform_escapes() -> None:
    for value in ("../secret", "/absolute", "data/../secret", r"data\secret", "C:/secret"):
        with pytest.raises(RestoreError):
            _normalize_member(value)


def test_restore_zip_extracts_only_allowlisted_regular_files(tmp_path: Path) -> None:
    archive = tmp_path / "state.zip"
    _write_zip(
        archive,
        {
            "data/shared_state.sqlite": b"sqlite",
            "data/soul_pending/proposal.json": b"{}",
            "data/wake_artifacts/result.txt": b"result",
            "munin/generated/tool.py": b"print('ok')\n",
        },
    )

    destination = tmp_path / "workspace"
    restored = restore_zip(archive, destination=destination)

    assert restored == [
        "data/shared_state.sqlite",
        "data/soul_pending/proposal.json",
        "data/wake_artifacts/result.txt",
        "munin/generated/tool.py",
    ]
    assert (destination / "data/shared_state.sqlite").read_bytes() == b"sqlite"
    assert (destination / "munin/generated/tool.py").read_text() == "print('ok')\n"


def test_restore_zip_rejects_non_allowlisted_member(tmp_path: Path) -> None:
    archive = tmp_path / "state.zip"
    _write_zip(archive, {"data/shared_state.sqlite": b"ok", "unexpected.txt": b"no"})

    with pytest.raises(RestoreError, match="non-allowlisted"):
        restore_zip(archive, destination=tmp_path / "workspace")


def test_restore_zip_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "state.zip"
    _write_zip(archive, {}, symlink="munin/generated/link")

    with pytest.raises(RestoreError, match="symbolic links"):
        restore_zip(archive, destination=tmp_path / "workspace")


def test_restore_zip_rejects_existing_symlink_parent(tmp_path: Path) -> None:
    archive = tmp_path / "state.zip"
    _write_zip(archive, {"munin/generated/tool.py": b"safe"})
    destination = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    (destination / "munin").mkdir(parents=True)
    (destination / "munin/generated").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RestoreError, match="symbolic-link parent"):
        restore_zip(archive, destination=destination)


def test_restore_zip_enforces_uncompressed_limit(tmp_path: Path) -> None:
    archive = tmp_path / "state.zip"
    _write_zip(archive, {"data/shared_state.sqlite": b"x" * 32})

    with pytest.raises(RestoreError, match="uncompressed limit"):
        restore_zip(
            archive,
            destination=tmp_path / "workspace",
            max_uncompressed_bytes=16,
        )
