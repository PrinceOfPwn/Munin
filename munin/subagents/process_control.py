# tags: [subagent, runtime, core, supervisor, orchestrator, workflow, scripts, discover_runner_pids, stop_detached_runners, _pid_alive, _signal_runner, process-lifecycle, proc-cmdline, sigterm-sigkill, process-group]
"""Process lifecycle helpers for detached Munin subagent runners."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("munin.subagents.process_control")
_RUNNER_MARKER = "munin.subagents.runner"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _cmdline(pid: int) -> str:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def discover_runner_pids() -> list[int]:
    """Discover only processes whose command line is the Munin runner module."""
    if os.name != "posix" or not Path("/proc").is_dir():
        return []
    current = os.getpid()
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid != current and _RUNNER_MARKER in _cmdline(pid):
            pids.append(pid)
    return sorted(set(pids))


def _signal_runner(pid: int, sig: signal.Signals) -> None:
    """Signal the detached runner's process group when it is the group leader."""
    try:
        pgid = os.getpgid(pid)
        if pgid == pid:
            os.killpg(pgid, sig)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    os.kill(pid, sig)


def stop_detached_runners(timeout: float = 15.0) -> dict[str, Any]:
    """Terminate and await every verified detached Munin runner."""
    pids = discover_runner_pids()
    for pid in pids:
        try:
            _signal_runner(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            logger.warning("could not SIGTERM runner pid=%d: %s", pid, exc)

    deadline = time.monotonic() + max(0.0, timeout)
    remaining = [pid for pid in pids if _pid_alive(pid)]
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = [pid for pid in remaining if _pid_alive(pid)]

    killed: list[int] = []
    for pid in remaining:
        try:
            _signal_runner(pid, signal.SIGKILL)
            killed.append(pid)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            logger.warning("could not SIGKILL runner pid=%d: %s", pid, exc)

    kill_deadline = time.monotonic() + 2.0
    still_alive = [pid for pid in remaining if _pid_alive(pid)]
    while still_alive and time.monotonic() < kill_deadline:
        time.sleep(0.1)
        still_alive = [pid for pid in still_alive if _pid_alive(pid)]

    return {
        "ok": not still_alive,
        "discovered": pids,
        "sigkilled": killed,
        "still_alive": still_alive,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stop detached Munin subagent runners")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    result = stop_detached_runners(timeout=args.timeout)
    print(json.dumps(result, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
