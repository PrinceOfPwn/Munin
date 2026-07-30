"""5 workers → aggregate has 5 entries."""
import pytest
pytest.importorskip("munin.core.parallel.send_workers")
pytest.importorskip("langgraph")

from munin.core.parallel.send_workers import fanout


def test_fanout_creates_correct_count():
    items = [{"host": f"10.0.0.{i}"} for i in range(5)]
    sends = fanout("worker_node", items)
    assert len(sends) == 5


def test_fanout_preserves_indices():
    items = ["a", "b", "c"]
    sends = fanout("worker", items)
    for i, send in enumerate(sends):
        assert send[1]["worker_index"] == i


def test_fanout_empty_list():
    assert fanout("worker", []) == []


def test_fanout_wraps_non_dict_items():
    sends = fanout("worker", ["plain_string"])
    assert sends[0][1]["task_args"] == {"payload": "plain_string"}
