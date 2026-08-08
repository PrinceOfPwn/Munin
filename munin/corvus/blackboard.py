# tags: [core, corvus, blackboard, publish, orchestration, fingerprint, atomic-index, deepagents-boundary, token-hygiene]
"""Corvus blackboard publish orchestration (GREEN for the committed RED contract).

The blackboard is an **external operative store** in the DeepAgents sense:
durable operational records live OUTSIDE the ephemeral LangGraph checkpoint.
DeepWiki (langchain-ai/deepagents) confirms the split between the in-graph
``StateBackend`` and the persistent ``StoreBackend``/``BaseStore``; Corvus
therefore owns no graph state, SQLite, ``SharedStateStore``, checkpoint, or
inbox. Everything here is expressed as Redis commands on the injected
transport.

Redis wire facts, verified via Context7 (Upstash Redis, ``/websites/upstash_redis``):

* ``SET key value NX`` claims exactly when a key is absent — used for the
  fingerprint reservation.
* ``XADD key * field value`` appends one stream entry per publish.
* ``ZADD key score member`` maintains a sorted-set index by epoch-millisecond
  score.
* ``/multi-exec`` (``pipeline(..., atomic=True)``) commits the whole batch as one
  transaction — all-or-nothing for the post record and every index.
* ``EVAL`` runs the Lua compare-and-delete so an owned reservation is released
  only when the stored value is still our own candidate id (never an unguarded
  ``DEL``).

The server remains the sole authority: caller-supplied fingerprints are
hashed into SHA-256 keys and the raw fingerprint never appears in a command,
error, or log surface.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from munin.corvus.contracts import CorvusContractError, Post, create_post
from munin.corvus.transport import CorvusTransportError, RedisTransport

__all__ = [
    "CorvusError",
    "CorvusConflictError",
    "CorvusNotFoundError",
    "CorvusBlackboard",
]

# Lua compare-and-delete: touch a reserved key only when the stored value is
# still the exact candidate id this board wrote. The fingerprint key is never
# dropped carelessly, so a lost race does not destroy another writer's claim.
_EVAL_COMPARE_DELETE = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] "
    "then return redis.call('DEL', KEYS[1]) "
    "else return 0 end"
)


class CorvusError(Exception):
    """Base failure for the Corvus blackboard; never echoes credentials."""


class CorvusConflictError(CorvusError):
    """A fingerprint reservation or stored record is missing or corrupt."""


class CorvusNotFoundError(CorvusError):
    """A referenced Corvus record does not exist."""


def _slugify_topic(raw: str) -> str:
    """Safe slug for a stripped, lowercase topic: only ``[a-z0-9_-]`` survives.

    Every run of disallowed characters becomes a single ``-``, repeated dashes
    collapse, leading/trailing dashes are trimmed, and a topic that slugs to
    nothing is rejected by the caller as blank.
    """
    lowered = raw.strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", lowered)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug


def _normalize_topics(topics: Iterable[str] | None) -> tuple[str, ...]:
    """Slug, dedupe (first-seen order), and reject blank/non-string topics.

    A bare ``str`` is treated as a caller error — topics must be an iterable of
    strings. Validation happens before any transport mutation so a bad topic
    can never touch Redis.
    """
    if topics is None:
        return ()
    if isinstance(topics, str):
        raise CorvusError("topics must be an iterable of strings")
    result: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not isinstance(topic, str):
            raise CorvusError("topics entries must be strings")
        slug = _slugify_topic(topic)
        if not slug:
            raise CorvusError("topics must not be blank")
        if slug not in seen:
            seen.add(slug)
            result.append(slug)
    return tuple(result)


def _sanitized(exc: Exception) -> CorvusError:
    """Bounded, credential-free ``CorvusError`` from a transport failure."""
    message = str(exc).strip().replace("\n", " ")
    if not message:
        message = "corvus transport failure"
    if len(message) > 200:
        message = message[:200] + "...[truncated]"
    return CorvusError(message)


class CorvusBlackboard:
    """Atomic publish orchestration over a Corvus Redis transport.

    A single ``publish`` claims a fingerprint (when supplied), then commits one
    atomic pipeline that writes the compact post JSON, appends the stream, and
    updates every index. On a fingerprint collision the stored post is fetched
    and validated instead of re-published; on an owned-reservation failure the
    reservation is compare-deleted and a sanitized ``CorvusError`` is raised.
    """

    def __init__(
        self,
        *,
        transport: RedisTransport,
        clock: Callable[[Any], datetime],
        timezone: str | ZoneInfo = "UTC",
        key_prefix: str = "munin:bb",
    ) -> None:
        if not isinstance(transport, RedisTransport):
            raise CorvusError("transport must implement RedisTransport")
        if not callable(clock):
            raise CorvusError("clock must be callable")
        prefix = key_prefix.strip().rstrip(":")
        if not prefix:
            raise CorvusError("key_prefix must not be blank")
        self._transport = transport
        self._clock = clock
        self._timezone = timezone
        self._key_prefix = prefix

    @property
    def key_prefix(self) -> str:
        """The normalized key prefix, stripped of any trailing colons."""
        return self._key_prefix

    # ------------------------------------------------------------------
    # Public — the single atomic publish orchestration.
    # ------------------------------------------------------------------

    def publish(
        self,
        *,
        post_type: Any,
        scope: Any,
        actor_id: str,
        content: str,
        topics: Iterable[str] | None = None,
        confidence: float | None = None,
        evidence_refs: Iterable[str] | None = None,
        investigation_refs: Iterable[str] | None = None,
        observed_at: datetime | None = None,
        expires_at: datetime | None = None,
        fingerprint: str | None = None,
    ) -> Post:
        """Publish one post atomically; returns the stored ``Post``.

        ``topics`` are normalized and deduped before any transport. With a
        ``fingerprint`` the flow first attempts ``SET key NX``; a lost (non-own)
        claim loads the existing post; a won claim proceeds to the atomic write
        and, on failure, best-effort compare-deletes the reservation.
        """
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or not fingerprint.strip()
        ):
            raise CorvusError("fingerprint must be a non-blank string")
        normalized_topics = _normalize_topics(topics)
        try:
            post = create_post(
                actor_id=actor_id,
                post_type=post_type,
                scope=scope,
                content=content,
                topics=normalized_topics,
                confidence=confidence,
                evidence_refs=evidence_refs,
                investigation_refs=investigation_refs,
                observed_at=observed_at,
                expires_at=expires_at,
                clock=self._clock,
                timezone=self._timezone,
            )
        except CorvusContractError as exc:
            raise CorvusError(str(exc)) from None

        fp_key: str | None = None
        if fingerprint is not None:
            fp_key = self._fingerprint_key(fingerprint)
            try:
                owned = self._transport.command(
                    "SET", fp_key, post.id, "NX"
                )
            except CorvusTransportError as exc:
                raise _sanitized(exc) from None
            if not owned:
                return self._load_existing(fp_key)

        try:
            self._transport.pipeline(
                self._pipeline_commands(post), atomic=True
            )
        except CorvusTransportError as exc:
            if fp_key is not None:
                self._release_reservation(fp_key, post.id)
            raise _sanitized(exc) from None
        return post

    # ------------------------------------------------------------------
    # Pipeline construction — one atomic multi for stream, indexes, post.
    # ------------------------------------------------------------------

    def _pipeline_commands(self, post: Post) -> list[list[Any]]:
        prefix = self._key_prefix
        score = int(post.published_at.timestamp() * 1000)
        commands: list[list[Any]] = [
            [
                "XADD",
                f"{prefix}:stream",
                "*",
                "post_id",
                post.id,
            ],
            ["ZADD", f"{prefix}:index:all", score, post.id],
            ["ZADD", f"{prefix}:index:state:{post.status.value}", score, post.id],
            ["ZADD", f"{prefix}:index:scope:{post.scope}", score, post.id],
            ["ZADD", f"{prefix}:index:actor:{post.actor_id}", score, post.id],
            [
                "ZADD",
                f"{prefix}:thread:{post.id}",
                score,
                self._thread_member(post),
            ],
        ]
        for topic in post.topics:
            commands.append(
                ["ZADD", f"{prefix}:index:topic:{topic}", score, post.id]
            )
        commands.append(
            [
                "SET",
                f"{prefix}:post:{post.id}",
                self._post_wire(post),
            ]
        )
        return commands

    def _thread_member(self, post: Post) -> str:
        return post.thread_root_id or post.id

    def _post_wire(self, post: Post) -> str:
        return json.dumps(
            post.to_wire(), sort_keys=True, separators=(",", ":")
        )

    # ------------------------------------------------------------------
    # Fingerprint collision handling.
    # ------------------------------------------------------------------

    def _load_existing(self, fp_key: str) -> Post:
        try:
            existing_id = self._coerce_utf8(
                self._transport.command("GET", fp_key)
            )
            stored = (
                self._transport.command("GET", self._post_key(existing_id))
                if existing_id
                else None
            )
        except CorvusTransportError as exc:
            raise _sanitized(exc) from None
        if not existing_id:
            raise CorvusConflictError(
                "fingerprint reserved but stored post is missing"
            ) from None
        if isinstance(stored, bytes):
            try:
                stored = stored.decode("utf-8")
            except UnicodeDecodeError:
                raise CorvusConflictError(
                    "fingerprint reserved but stored post is corrupt"
                ) from None
        if not stored:
            raise CorvusConflictError(
                "fingerprint reserved but stored post is missing"
            ) from None
        try:
            wire = json.loads(stored)
        except ValueError:
            raise CorvusConflictError(
                "fingerprint reserved but stored post is corrupt"
            ) from None
        try:
            return Post.model_validate(wire)
        except (ValueError, TypeError):
            raise CorvusConflictError(
                "fingerprint reserved but stored post is invalid"
            ) from None

    def _coerce_utf8(self, value: Any) -> str:
        """Normalize a transport-read id (possibly bytes) to a UTF-8 string."""
        if value is None:
            return ""
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                raise CorvusConflictError(
                    "fingerprint reserved but stored id is not valid UTF-8"
                ) from None
        return str(value)

    def _release_reservation(self, fp_key: str, candidate_id: str) -> None:
        try:
            self._transport.command(
                "EVAL", _EVAL_COMPARE_DELETE, 1, fp_key, candidate_id
            )
        except CorvusTransportError:
            # Best-effort only; a failed release must not mask the publish error.
            pass

    # ------------------------------------------------------------------
    # Key helpers.
    # ------------------------------------------------------------------

    def _fingerprint_key(self, fingerprint: str) -> str:
        """SHA-256 of the fingerprint only; the raw value never leaves caller."""
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}:fingerprint:{digest}"

    def _post_key(self, post_id: str) -> str:
        return f"{self._key_prefix}:post:{post_id}"