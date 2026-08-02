# tags: [mcp, database, sqlite, turso, persistence, open_connection, _LibsqlConnectionProxy, LibsqlConnectionPool, open_pooled_connection, _classify, _RowProxy, _LibsqlCursorProxy, PoolCheckoutTimeout, describe_backend, _split_script]
"""Munin storage abstraction — local SQLite by default, Turso libsql opt-in.

Why this layer exists
---------------------
Munin was built around SQLite via `sqlite3.connect(path)`. That's perfect for a
long-lived host but breaks the moment you run it on ephemeral infra
(GitHub Actions, Fly.io Machines, Vercel-style functions): the file lives inside
the runner's disk and dies with it. Every session starts blank — soul doesn't
evolve, memory doesn't accumulate, forged tools disappear.

This module lets you swap the backing store with a single environment variable
(``MUNIN_DB_URL``) without touching call sites:

  * unset / empty  →  local file at ``$MUNIN_DATA_PATH/shared_state.sqlite``
  * ``file:/path`` →  local file at that path
  * ``libsql://<host>`` + auth token    →  direct Turso connection
  * ``libsql+file:/path``               →  local libsql database (rarely useful)

Public API mirrors the subset of ``sqlite3`` we actually use:
- ``open_connection(url_or_path)`` returns a ``ConnectionProxy``
- ``ConnectionProxy`` supports ``execute`` / ``executescript`` / ``commit`` /
  ``rollback`` / iteration via ``fetchall``/``fetchone`` on cursor
- Cursor rows are dicts (works like ``sqlite3.Row`` with ``row["col"]``)

The libsql backend is used ONLY when the URL indicates so. Local file paths
keep using the stdlib driver — zero perf regression for the default case.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("munin.persistence")

# Lazy import: the official libsql driver is only required if MUNIN_DB_URL
# points at a libsql:// endpoint. Falling back to a helpful error otherwise.
_libsql = None
try:  # pragma: no cover - environment-dependent import
    import libsql as _libsql  # type: ignore[import-not-found]
except Exception:
    _libsql = None


# ─────────────────────────────────────────────────────────────────────────────
# URL parsing
# ─────────────────────────────────────────────────────────────────────────────

def _classify(url_or_path: str) -> tuple[str, dict[str, Any]]:
    """Return (backend, params) where backend is one of: 'sqlite', 'libsql'.

    Empty / plain paths are treated as 'sqlite'. Strings starting with ``libsql://``
    or ``libsql+file:`` route to the libsql backend.
    """
    raw = (url_or_path or "").strip()
    if not raw:
        return "sqlite", {"path": ""}  # caller must resolve default

    lowered = raw.lower()
    if lowered.startswith("libsql://") or lowered.startswith("libsqls://"):
        parsed = urlparse(raw)
        # libsql://<host>[:port][/db]?authToken=xxx
        qs = parse_qs(parsed.query)
        auth_token = qs.get("authToken", [""])[0] or qs.get("auth_token", [""])[0]
        # Reconstruct without the credentials so we don't log them:
        clean_url = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            clean_url += f":{parsed.port}"
        if parsed.path and parsed.path != "/":
            clean_url += parsed.path
        return "libsql", {"url": clean_url, "auth_token": auth_token, "raw": raw}

    if lowered.startswith("libsql+file:"):
        return "libsql", {"path": raw[len("libsql+file:"):]}

    if lowered.startswith("file:"):
        return "sqlite", {"path": raw[len("file:"):]}

    # Plain path
    return "sqlite", {"path": raw}


# ─────────────────────────────────────────────────────────────────────────────
# Cursor & Connection proxies
# ─────────────────────────────────────────────────────────────────────────────

class _RowProxy:
    """Emulates ``sqlite3.Row``: supports ``row["col"]`` and ``row[i]``.

    libsql returns tuples with an accompanying `description` on the
    cursor. We wrap those into dict-like rows so existing code that reads
    ``row["target_ip"]`` keeps working.
    """
    __slots__ = ("_data", "_keys")

    def __init__(self, data: tuple[Any, ...], keys: list[str]) -> None:
        self._data = data
        self._keys = keys

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._data[key]
        try:
            idx = self._keys.index(key)
        except ValueError as exc:
            raise KeyError(key) from exc
        return self._data[idx]

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def keys(self) -> list[str]:
        return list(self._keys)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"_RowProxy({dict(zip(self._keys, self._data, strict=True))!r})"


class _LibsqlCursorProxy:
    """Wraps a libsql cursor to look and behave like a sqlite3 cursor."""

    def __init__(self, cursor: Any) -> None:
        self._cur = cursor

    @property
    def lastrowid(self) -> int:
        return int(self._cur.lastrowid)

    @property
    def rowcount(self) -> int:
        """Number of rows affected by the last UPDATE/DELETE/INSERT.

        Every soft-delete path in ``shared_state.py`` (``deactivate_generated_tool``,
        ``graph_drop``, ``procedural_purge_*``) reads this to decide success —
        without the proxy, libsql callers crashed with ``AttributeError`` on
        every drop. Falls back to ``-1`` if the underlying driver doesn't
        expose it.
        """
        try:
            return int(self._cur.rowcount)
        except (AttributeError, TypeError):
            return -1

    def _col_names(self) -> list[str]:
        desc = getattr(self._cur, "description", None) or []
        return [d[0] for d in desc]

    def fetchone(self) -> _RowProxy | None:
        row = self._cur.fetchone()
        if row is None:
            return None
        return _RowProxy(tuple(row), self._col_names())

    def fetchall(self) -> list[_RowProxy]:
        keys = self._col_names()
        return [_RowProxy(tuple(r), keys) for r in self._cur.fetchall()]

    def __iter__(self) -> Iterator[_RowProxy]:
        keys = self._col_names()
        for r in self._cur:
            yield _RowProxy(tuple(r), keys)


class _LibsqlConnectionProxy:
    """Wraps a libsql connection to look like a sqlite3 connection.

    When ``_pool`` is provided the proxy is a **pooled checkout**: ``close()``
    hands the underlying native connection back to the pool instead of tearing
    down the TLS+Hrana session, saving 200-500ms of handshake per operation.
    """

    def __init__(
        self,
        native_conn: Any,
        *,
        sync_on_commit: bool = True,
        _pool: LibsqlConnectionPool | None = None,
    ) -> None:
        self._conn = native_conn
        self._sync_on_commit = sync_on_commit
        self._pool = _pool
        # Guards against double-close: once returned to the pool the proxy is
        # dead; another ``.close()`` (or a stray ``execute``) must not race
        # with a fresh checkout of the same native handle.
        self._released = False

    def execute(self, sql: str, params: Any = ()) -> _LibsqlCursorProxy:
        if self._released:
            raise RuntimeError("libsql connection was already returned to the pool")
        return _LibsqlCursorProxy(self._conn.execute(sql, params))

    def executescript(self, sql: str) -> _LibsqlCursorProxy:
        if self._released:
            raise RuntimeError("libsql connection was already returned to the pool")
        # libsql doesn't have executescript — split on ; naively but respect strings.
        # For our schema this is fine (no ; inside DDL literals).
        for stmt in _split_script(sql):
            if stmt.strip():
                self._conn.execute(stmt)
        return _LibsqlCursorProxy(self._conn.cursor())

    def commit(self) -> None:
        self._conn.commit()
        # Embedded replicas do not publish local writes until ``sync`` runs.
        # Keeping it behind the sqlite-compatible ``commit`` boundary makes
        # every successful Munin transaction durable in Turso before returning.
        if self._sync_on_commit:
            self._conn.sync()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        if self._pool is not None:
            # Return-to-pool path. The pool decides whether the native handle
            # is healthy enough to be reused; if not, it discards it and
            # spawns a replacement in a background-safe manner.
            try:
                self._pool.checkin(self._conn)
            except Exception as exc:  # pragma: no cover - pool shutdown race
                logger.debug("libsql pool checkin failed: %s", exc)
            self._conn = None
            return
        try:
            self._conn.close()
        except Exception as exc:  # pragma: no cover - driver shutdown guard
            logger.debug("libsql close failed: %s", exc)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.commit()
            except Exception as commit_exc:
                logger.warning("libsql context commit failed: %s", commit_exc)
                raise
        else:
            try:
                self._conn.rollback()
            except Exception as rollback_exc:  # pragma: no cover - driver shutdown guard
                logger.warning("libsql context rollback failed: %s", rollback_exc)
        return False


def _strip_sql_comments(sql: str) -> str:
    """Remove ``-- ...`` line comments and ``/* ... */`` block comments.

    Preserves quoted string literals verbatim. Needed because our naive
    ``_split_script`` treats every ``;`` as a boundary; a comment like
    ``-- it's stale; but usable`` would open a quote (the apostrophe),
    slurp the following statements as "in-string", and produce nonsense.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    in_string = False
    quote_char = ""
    while i < n:
        ch = sql[i]
        if in_string:
            out.append(ch)
            if ch == quote_char and (i == 0 or sql[i - 1] != "\\"):
                in_string = False
            i += 1
            continue
        # Not in a string — check for comment openers.
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            # skip to newline
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch in ("'", '"'):
            in_string = True
            quote_char = ch
        out.append(ch)
        i += 1
    return "".join(out)


def _split_script(sql: str) -> list[str]:
    """Naive statement splitter for schema DDL.

    Strips SQL comments FIRST (so a ``--`` line with a stray ``;`` or ``'``
    can't corrupt the tokenizer), then splits on top-level ``;`` while
    respecting string literals. Sufficient for our fixed schema; upgrade to
    sqlglot if we ever add triggers with ``BEGIN…END`` blocks.
    """
    sql = _strip_sql_comments(sql)
    out: list[str] = []
    buf: list[str] = []
    in_string = False
    quote_char = ""
    for ch in sql:
        if in_string:
            buf.append(ch)
            if ch == quote_char:
                in_string = False
        else:
            if ch in ("'", '"'):
                in_string = True
                quote_char = ch
                buf.append(ch)
            elif ch == ";":
                out.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [s for s in (x.strip() for x in out) if s]


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def open_connection(
    url_or_path: str,
    *,
    default_path: Path | None = None,
    auth_token: str = "",
    authoritative: bool = False,
) -> Any:
    """Return a connection object compatible with the subset of sqlite3 Munin uses.

    * Plain paths / ``file:`` URIs → ``sqlite3.Connection`` with WAL + Row factory.
    * ``libsql://`` URLs → a direct, autocommit Turso connection. Munin's
      persistent state is authoritative online state; using an embedded replica
      on disposable GitHub runners let the replica's Hrana stream expire during
      long ReAct jobs and made a successful forge depend on an artifact upload.

    Callers can use ``with open_connection(url) as conn: conn.execute(...)`` or
    the connection as a plain object. Cursor rows behave like ``sqlite3.Row``.
    """
    backend, params = _classify(url_or_path)

    if backend == "sqlite":
        path_str = params.get("path", "")
        if not path_str:
            if default_path is None:
                raise RuntimeError("open_connection: empty URL and no default_path")
            path = default_path
        else:
            path = Path(path_str).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    # libsql backend
    if _libsql is None:
        raise RuntimeError(
            "MUNIN_DB_URL points at libsql:// but the official libsql package is not installed. "
            "Install the project dependencies or run `pip install libsql`. "
            "Or unset MUNIN_DB_URL to fall back to local SQLite."
        )

    if "url" in params:
        # Always use Turso directly. The previous embedded-replica path stored
        # mutable state on an ephemeral Actions runner and could surface
        # ``Hrana: stream not found`` after a long-running agent. Autocommit
        # keeps each database operation short-lived and remote-authoritative.
        native = _libsql.connect(
            database=params["url"],
            auth_token=auth_token or params.get("auth_token") or "",
            isolation_level=None,
        )
        return _LibsqlConnectionProxy(native, sync_on_commit=False)
    # libsql+file:
    native = _libsql.connect(params["path"])
    # libsql doesn't need journal_mode/synchronous — Turso handles it server-side.
    return _LibsqlConnectionProxy(native)


def describe_backend(url_or_path: str) -> str:
    """Return a human-readable backend descriptor for logs (no secrets)."""
    backend, params = _classify(url_or_path)
    if backend == "sqlite":
        p = params.get("path") or "(default)"
        return f"sqlite({p})"
    if "url" in params:
        return f"libsql({params['url']})"
    return f"libsql-local({params.get('path','?')})"


# ─────────────────────────────────────────────────────────────────────────────
# Fase 5: bounded libsql connection pool
# ─────────────────────────────────────────────────────────────────────────────
#
# Without pooling every DurableStore operation opens a new libsql session, and
# each session costs a full TLS + Hrana handshake (~200-500 ms). A pool of N
# pre-warmed connections amortises that cost across all requests while capping
# the fan-out to Turso so we never accidentally DoS ourselves during a burst.
#
# Semantics:
#   * Pre-created at construction (N native connections).
#   * ``checkout()`` blocks on ``queue.Queue.get`` (thread-safe by design) with
#     a bounded timeout — better to fail-fast than hang a request forever.
#   * A cheap ``SELECT 1`` health check verifies the handle is alive; broken
#     connections are discarded and transparently replaced.
#   * ``close_all()`` drains the pool and tears down every native handle so
#     the ASGI shutdown hook can leave nothing behind.
#   * After ``close_all`` further checkouts raise ``RuntimeError`` — that's
#     the expected shape during a rolling restart and callers surface it as
#     ``503 Service Unavailable`` via the normal error path.


class PoolCheckoutTimeout(RuntimeError):
    """Raised when no connection becomes available within the configured window."""


class LibsqlConnectionPool:
    """A tiny bounded pool for libsql native connections.

    Parameters
    ----------
    factory:
        Callable that yields a fresh **native** libsql connection every time.
        The pool owns whatever the factory returns.
    size:
        Number of connections to pre-create and cap concurrency at.
    timeout_s:
        Upper bound for a blocking ``checkout()`` before ``PoolCheckoutTimeout``
        is raised. Keeping this well below the request timeout lets a
        saturated pool surface as a 503 instead of a hung request.
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        *,
        size: int = 4,
        timeout_s: float = 10.0,
    ) -> None:
        if size < 1:
            raise ValueError("pool size must be >= 1")
        self._factory = factory
        self._size = int(size)
        self._timeout_s = float(timeout_s)
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=self._size)
        self._all: list[Any] = []
        self._lock = threading.Lock()
        self._closed = False
        # Pre-warm every slot. If any single connection fails to open we tear
        # the rest down so the boot path fails loud rather than limping along
        # with a half-populated pool.
        try:
            for _ in range(self._size):
                conn = self._factory()
                self._all.append(conn)
                self._queue.put_nowait(conn)
        except Exception:
            self.close_all()
            raise

    @property
    def size(self) -> int:
        return self._size

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    def _ping(self, conn: Any) -> bool:
        """Cheap round-trip health check. Any exception means 'discard me'."""
        try:
            cur = conn.execute("SELECT 1")
            # libsql cursors expose fetchone(); result is unused.
            with suppress(Exception):
                cur.fetchone()
            return True
        except Exception:
            return False

    def _replace_broken(self, broken: Any) -> Any:
        """Discard ``broken`` and spawn a replacement (still holds the slot)."""
        with suppress(Exception):
            broken.close()
        with self._lock:
            with suppress(ValueError):
                self._all.remove(broken)
            replacement = self._factory()
            self._all.append(replacement)
        return replacement

    def checkout(self) -> Any:
        """Block up to ``timeout_s`` waiting for a healthy native connection."""
        if self._closed:
            raise RuntimeError("libsql pool is closed")
        try:
            conn = self._queue.get(timeout=self._timeout_s)
        except queue.Empty as exc:
            raise PoolCheckoutTimeout(
                f"libsql pool checkout timed out after {self._timeout_s:.1f}s "
                f"(size={self._size})"
            ) from exc
        # Health check the moment we own the slot. If the underlying TCP was
        # reset while idle (Turso side, LB idle timeout, kernel drop) this
        # transparently rebuilds it before the caller sees anything.
        if not self._ping(conn):
            try:
                conn = self._replace_broken(conn)
            except Exception:
                # Can't rebuild — surface the failure; slot stays "outstanding"
                # until the caller either checks something back in or shutdown
                # drains the pool. We do NOT re-queue a dead handle.
                raise
        return conn

    def checkin(self, conn: Any) -> None:
        """Return a connection to the pool. Silently discards if closed."""
        if self._closed or conn is None:
            with suppress(Exception):
                if conn is not None:
                    conn.close()
            return
        try:
            self._queue.put_nowait(conn)
        except queue.Full:
            # More checkouts happened than the pool tracks — shouldn't happen,
            # but defensively close the extra handle instead of leaking it.
            with suppress(Exception):
                conn.close()
            with self._lock, suppress(ValueError):
                self._all.remove(conn)

    def close_all(self) -> None:
        """Tear down every native handle. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Drain the queue so no one grabs a handle mid-shutdown.
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            for conn in list(self._all):
                with suppress(Exception):
                    conn.close()
            self._all.clear()


def open_pooled_connection(
    url_or_path: str,
    *,
    default_path: Path | None = None,
    auth_token: str = "",
    pool_size: int = 4,
    pool_timeout_s: float = 10.0,
) -> tuple[Callable[[], Any], LibsqlConnectionPool | None]:
    """Build a factory + optional pool for the configured backend.

    For ``libsql://`` URLs this pre-creates ``pool_size`` native connections
    and returns a factory that hands out pooled ``_LibsqlConnectionProxy``
    instances (``close()`` returns the underlying handle to the pool).

    For local sqlite / ``file:`` / ``libsql+file:`` URLs this returns an
    unpooled factory identical in shape to :func:`open_connection` — those
    backends don't benefit from pooling (sqlite3 WAL handles concurrency
    locally; libsql+file has no network round-trip to amortise).

    The returned pool (or ``None``) MUST be closed via ``pool.close_all()``
    at process shutdown to avoid leaking sockets.
    """
    backend, params = _classify(url_or_path)
    if backend == "libsql" and "url" in params:
        if _libsql is None:
            raise RuntimeError(
                "MUNIN_DB_URL points at libsql:// but the official libsql package is not installed. "
                "Install the project dependencies or run `pip install libsql`."
            )
        target_url = params["url"]
        native_token = auth_token or params.get("auth_token") or ""

        def _native() -> Any:
            return _libsql.connect(
                database=target_url,
                auth_token=native_token,
                isolation_level=None,
            )

        pool = LibsqlConnectionPool(_native, size=pool_size, timeout_s=pool_timeout_s)

        def factory() -> Any:
            native = pool.checkout()
            return _LibsqlConnectionProxy(native, sync_on_commit=False, _pool=pool)

        return factory, pool

    # Non-pooled path: preserve the existing per-call semantics of
    # ``open_connection`` so sqlite and libsql+file behave exactly like before.
    def factory() -> Any:
        return open_connection(
            url_or_path,
            default_path=default_path,
            auth_token=auth_token,
            authoritative=True,
        )

    return factory, None
