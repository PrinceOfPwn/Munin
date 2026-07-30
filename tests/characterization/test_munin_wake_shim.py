"""runner.py is now a thin shim — no subprocess spawning."""
import ast
import pytest
from pathlib import Path

RUNNER_PATH = Path(__file__).parent.parent.parent / "munin/subagents/runner.py"


def test_runner_has_no_subprocess_import():
    if not RUNNER_PATH.exists():
        pytest.skip("runner.py not found")
    source = RUNNER_PATH.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in getattr(node, "names", [])]
            module = getattr(node, "module", "") or ""
            assert "subprocess" not in names and "subprocess" not in module


def test_runner_has_start_async_task():
    if not RUNNER_PATH.exists():
        pytest.skip("runner.py not found")
    assert "start_async_task" in RUNNER_PATH.read_text()


def test_store_v3_5_migration():
    pytest.importorskip("munin.production.store")
    import tempfile
    from munin.production.store import ProductionStore, MIGRATION_ID

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = ProductionStore(db_path)
    assert MIGRATION_ID == "v3.5"
    run_id = store.create_run("conv-1", goal="test")
    assert run_id
    run = store.get_run(run_id)
    assert run["state"] == "running"
