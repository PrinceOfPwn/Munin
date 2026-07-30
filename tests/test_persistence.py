from __future__ import annotations


class _FakeNative:
    def __init__(self) -> None:
        self.synced = False
        self.sync_count = 0
        self.committed = False
        self.closed = False

    def sync(self) -> None:
        self.synced = True
        self.sync_count += 1

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


class _FakeLibsql:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.native = _FakeNative()

    def connect(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.native


def test_turso_uses_direct_autocommit_connection(tmp_path, monkeypatch):
    from munin.mcp import persistence

    fake = _FakeLibsql()
    monkeypatch.setattr(persistence, "_libsql", fake)
    conn = persistence.open_connection(
        "libsql://munin-example.turso.io",
        default_path=tmp_path / "shared_state.sqlite",
        auth_token="secret-token",  # noqa: S106 - inert fake-driver fixture
    )

    args, kwargs = fake.calls[0]
    assert args == ()
    assert kwargs == {
        "database": "libsql://munin-example.turso.io",
        "auth_token": "secret-token",
        "isolation_level": None,
    }
    assert fake.native.synced is False
    conn.commit()
    assert fake.native.committed is True
    assert fake.native.sync_count == 0
    conn.close()
    assert fake.native.closed is True


def test_turso_authoritative_connection_uses_same_direct_contract(tmp_path, monkeypatch):
    from munin.mcp import persistence

    fake = _FakeLibsql()
    monkeypatch.setattr(persistence, "_libsql", fake)
    conn = persistence.open_connection(
        "libsql://munin-example.turso.io",
        default_path=tmp_path / "shared_state.sqlite",
        auth_token="secret-token",  # noqa: S106 - inert fake-driver fixture
        authoritative=True,
    )

    args, kwargs = fake.calls[0]
    assert args == ()
    assert kwargs == {
        "database": "libsql://munin-example.turso.io",
        "auth_token": "secret-token",
        "isolation_level": None,
    }
    assert fake.native.sync_count == 0
    conn.commit()
    assert fake.native.committed is True
    assert fake.native.sync_count == 0


def test_backend_description_never_contains_query_token():
    from munin.mcp.persistence import describe_backend

    description = describe_backend("libsql://munin-example.turso.io?authToken=top-secret")
    assert description == "libsql(libsql://munin-example.turso.io)"
    assert "top-secret" not in description
