"""Munin CLI — `munin run`, `munin mcp`, `munin reset`, `munin subagent`, `munin ldap-seed`."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import click

from .mcp.config import get_settings, redact_settings
from .mcp.shared_state import SharedStateStore

logging.basicConfig(level=logging.INFO, format="[munin] %(levelname)s %(message)s")
logger = logging.getLogger("munin.cli")


@click.group()
def cli() -> None:
    """Munin — ReAct offensive-security agent with soul + memory + MCP."""


# ---------------------------------------------------------------------------
# munin run
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--max-iterations", default=8, show_default=True)
def run(max_iterations: int) -> None:
    """Interactive REPL. Each user line is one Munin turn."""
    from .core.munin_agent import MuninAgent

    settings = get_settings()
    click.echo(f"Munin online — LLM={settings.llm_model or 'UNCONFIGURED'} base_url={settings.llm_base_url or 'UNCONFIGURED'}")
    click.echo(f"Preflight policy: {settings.preflight_policy}")
    if not settings.llm_base_url or not settings.llm_api_key or not settings.llm_model:
        click.echo("!! LLM_* env variables missing. Populate .env before running.", err=True)
        sys.exit(2)

    agent = MuninAgent(settings)
    try:
        while True:
            user_line = click.prompt("you", type=str, default="", show_default=False)
            if not user_line.strip():
                continue
            if user_line.strip().lower() in {"exit", "quit"}:
                click.echo("bye.")
                return
            result = agent.respond(user_line.strip(), max_iterations=max_iterations)
            click.echo(f"munin> {result['content']}")
    except (KeyboardInterrupt, EOFError):
        click.echo("\ninterrupted.")


# ---------------------------------------------------------------------------
# munin mcp
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--transport", default="stdio", type=click.Choice(["stdio", "sse", "streamable-http"]))
@click.option("--host", default="")
@click.option("--port", type=int, default=0)
def mcp(transport: str, host: str, port: int) -> None:
    """Start the Munin MCP server."""
    from .mcp import main as mcp_main

    argv = ["--transport", transport]
    if host:
        argv.extend(["--host", host])
    if port:
        argv.extend(["--port", str(port)])
    sys.argv = ["munin-mcp", *argv]
    mcp_main.main()


# ---------------------------------------------------------------------------
# munin subagent
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("name")
@click.option("--sleep-after-idle", default=120, show_default=True)
def subagent(name: str, sleep_after_idle: int) -> None:
    """Run a subagent locally (equivalent to `python -m munin.subagents.runner <name>`)."""
    proc = subprocess.run(
        [sys.executable, "-m", "munin.subagents.runner", name, "--sleep-after-idle", str(sleep_after_idle)],
        check=False,
    )
    sys.exit(proc.returncode)


# ---------------------------------------------------------------------------
# munin reset
# ---------------------------------------------------------------------------

@cli.command()
@click.confirmation_option(prompt="This wipes memory + generated tools + graphs and restores soul. Continue?")
def reset() -> None:
    """Reset Munin to its snapshot state (idempotent)."""
    from .core.soul import SoulManager
    from .mcp import registry

    settings = get_settings()
    state = SharedStateStore(settings)

    # Purge generated tools + graphs.
    purged_tools = registry.purge_all(state)
    purged_graphs = state.graph_purge_on_reset()

    # Remove script files under generated/
    gen_dir: Path = settings.generated_tools_dir
    files_removed = 0
    if gen_dir.exists():
        for path in gen_dir.iterdir():
            if path.is_file() and path.suffix == ".py":
                path.unlink()
                files_removed += 1

    # Wipe episodic/semantic (memory of prior life).
    with state._connect() as conn:  # intentional private access — reset is privileged
        conn.execute("DELETE FROM episodic")
        conn.execute("DELETE FROM semantic")
        conn.execute("DELETE FROM agent_wake_queue")

    # Restore soul.
    soul = SoulManager(settings.munin_soul_path, settings.munin_data_path)
    if soul.snapshot_path.exists():
        soul_report = soul.restore()
    else:
        soul_report = {"warning": "no soul snapshot to restore — leaving soul/ as-is"}

    pending_cleared = soul.clear_pending_edits()
    click.echo(json.dumps({
        "purged_tools": purged_tools,
        "purged_graphs": purged_graphs,
        "script_files_removed": files_removed,
        "soul": soul_report,
        "pending_cleared": pending_cleared,
    }, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# munin ldap-seed
# ---------------------------------------------------------------------------

@cli.command("ldap-seed")
def ldap_seed() -> None:
    """Bring up the OpenLDAP challenge container (docker compose)."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "seed_ldap.sh"
    if not script.exists():
        click.echo("scripts/seed_ldap.sh not found — copy from repo root.", err=True)
        sys.exit(2)
    proc = subprocess.run(["bash", str(script)], check=False)
    sys.exit(proc.returncode)


# ---------------------------------------------------------------------------
# munin ldap-mock
# ---------------------------------------------------------------------------

@cli.group("ldap-mock")
def ldap_mock() -> None:
    """Manage the local mock LDAP server (Docker). Pre-seeded with Kerberoastable,
    AS-REP Roastable and Domain Admin scenarios for testing Munin's LDAP tools."""


@ldap_mock.command("up")
@click.option("--port", default=389, show_default=True, help="Host port to bind (maps to container 1389).")
def ldap_mock_up(port: int) -> None:
    """Start the mock LDAP container and seed test data."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "ldap_mock.sh"
    if not script.exists():
        click.echo("scripts/ldap_mock.sh not found.", err=True)
        sys.exit(2)
    import os
    env = {**os.environ, "LDAP_MOCK_PORT": str(port)}
    proc = subprocess.run(["bash", str(script), "up"], check=False, env=env)
    sys.exit(proc.returncode)


@ldap_mock.command("down")
def ldap_mock_down() -> None:
    """Stop and remove the mock LDAP container."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "ldap_mock.sh"
    proc = subprocess.run(["bash", str(script), "down"], check=False)
    sys.exit(proc.returncode)


@ldap_mock.command("status")
def ldap_mock_status() -> None:
    """Show whether the mock LDAP container is running and entry counts."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "ldap_mock.sh"
    proc = subprocess.run(["bash", str(script), "status"], check=False)
    sys.exit(proc.returncode)


@ldap_mock.command("logs")
def ldap_mock_logs() -> None:
    """Tail recent logs from the mock LDAP container."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "ldap_mock.sh"
    proc = subprocess.run(["bash", str(script), "logs"], check=False)
    sys.exit(proc.returncode)


# ---------------------------------------------------------------------------
# munin snapshot-soul
# ---------------------------------------------------------------------------

@cli.command("snapshot-soul")
def snapshot_soul() -> None:
    """Freeze soul/ into data/soul.snapshot.json. Idempotent."""
    from .core.soul import SoulManager

    settings = get_settings()
    soul = SoulManager(settings.munin_soul_path, settings.munin_data_path)
    report = soul.snapshot()
    click.echo(json.dumps(report, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# munin config
# ---------------------------------------------------------------------------

@cli.command()
def config() -> None:
    """Show the current settings (secrets redacted)."""
    settings = redact_settings(get_settings())
    click.echo(json.dumps({k: (str(v) if isinstance(v, Path) else v) for k, v in settings.__dict__.items()}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
