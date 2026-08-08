# tags: [store, reconciliation, issue-18, issue-32, discord, hitl, indexes, runtime]
"""Reconcile store contracts that evolved on both sides of the frontend stack.

The Issue #18/#32 frontend stack and the later Discord/runtime work both
changed ``store.py`` after their common ancestor.  The unified PR deliberately
keeps the complete Issue #18 implementation of that large module, then applies
this small, explicit compatibility layer at package import time so post-fork
main behavior is not lost.

Keeping the overlap isolated here makes every preserved contract reviewable:
composite production indexes, Discord community participants/HITL APIs, admin
HITL authority, and the active-run guidance guard from PR #71.
"""

from __future__ import annotations

import hmac
import json
from typing import Any


def apply_store_reconciliation(store: Any) -> None:
    """Apply the unified-branch store delta once, before any store is built."""
    if getattr(store, "_UNIFIED_STORE_RECONCILED", False):
        return

    # PR #40 review: indexes must match the *ordered* production queries, not
    # merely the first WHERE column.  The names intentionally differ from the
    # old single-column indexes so existing deployments can add them forward-
    # only and SQLite may choose the stronger shape immediately.
    store._PLAN18_DDL = (
        """CREATE INDEX IF NOT EXISTS idx_conversation_participants_user ON conversation_participants(user_id, removed_at_ms)""",
        """CREATE INDEX IF NOT EXISTS idx_tool_calls_run_started ON tool_calls(run_id, started_at_ms, id)""",
        """CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation ON agent_runs(conversation_id)""",
        """CREATE INDEX IF NOT EXISTS idx_reasoning_events_run_created ON reasoning_events(run_id, kind, created_at_ms, id)""",
        """CREATE INDEX IF NOT EXISTS idx_human_requests_run_created ON human_requests(run_id, created_at_ms, id)""",
        """CREATE INDEX IF NOT EXISTS idx_subagent_runs_parent_started ON subagent_runs(parent_run_id, started_at_ms, id)""",
        """CREATE INDEX IF NOT EXISTS idx_conversation_artifacts_run_created ON conversation_artifacts(run_id, created_at_ms, id)""",
        """CREATE INDEX IF NOT EXISTS idx_conversation_summaries_conv ON conversation_summaries(conversation_id)""",
        """CREATE INDEX IF NOT EXISTS idx_conversation_summaries_run_created ON conversation_summaries(run_id, created_at_ms, id)""",
    )

    ProductionStore = store.ProductionStore
    MuninStore = store.MuninStore

    def add_conversation_participant(
        self: Any, *, conversation_id: str, user_id: str, role: str = "member"
    ) -> dict[str, Any]:
        """Add/re-enable one participant in a shared conversation graph."""
        if role not in {"owner", "member"}:
            raise ValueError("participant role must be owner or member")
        now = store._now_ms()
        with self._transaction() as conn:
            self._require_user(conn, user_id)
            if not conn.execute(
                "SELECT 1 FROM conversations WHERE id=? AND deleted_at_ms IS NULL",
                (conversation_id,),
            ).fetchone():
                raise KeyError(conversation_id)
            conn.execute(
                "INSERT INTO conversation_participants "
                "(conversation_id,user_id,role,added_at_ms,removed_at_ms) VALUES (?,?,?,?,NULL) "
                "ON CONFLICT(conversation_id,user_id) DO UPDATE SET "
                "role=excluded.role,removed_at_ms=NULL",
                (conversation_id, user_id, role, now),
            )
            self._audit(
                conn,
                actor_id=user_id,
                action="conversation.participant_added",
                resource_type="conversation",
                resource_id=conversation_id,
                outcome="success",
            )
        return {"conversation_id": conversation_id, "user_id": user_id, "role": role}

    def list_pending_human_requests(
        self: Any, *, actor_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return pending HITL rows visible to a participant or administrator."""
        now = store._now_ms()
        cap = max(1, min(int(limit), 100))
        with self._read_only() as conn:
            if self._is_admin(conn, actor_id):
                rows = conn.execute(
                    """SELECT h.id,h.run_id,h.action,h.risk,h.choices_json,
                              h.expires_at_ms,h.created_at_ms,r.conversation_id
                       FROM human_requests h JOIN agent_runs r ON r.id=h.run_id
                       WHERE h.state='waiting' AND h.expires_at_ms>?
                       ORDER BY h.created_at_ms DESC LIMIT ?""",
                    (now, cap),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT h.id,h.run_id,h.action,h.risk,h.choices_json,
                              h.expires_at_ms,h.created_at_ms,r.conversation_id
                       FROM human_requests h JOIN agent_runs r ON r.id=h.run_id
                       JOIN conversation_participants p ON p.conversation_id=r.conversation_id
                       WHERE h.state='waiting' AND h.expires_at_ms>?
                         AND p.user_id=? AND p.removed_at_ms IS NULL
                       ORDER BY h.created_at_ms DESC LIMIT ?""",
                    (now, actor_id, cap),
                ).fetchall()
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "action": row["action"],
                "risk": row["risk"],
                "choices": json.loads(row["choices_json"]),
                "expires_at_ms": int(row["expires_at_ms"]),
                "created_at_ms": int(row["created_at_ms"]),
                "conversation_id": row["conversation_id"],
            }
            for row in rows
        ]

    def resolve_human_decision(
        self: Any,
        *,
        actor_id: str,
        request_id: str,
        choice: str,
        nonce: str,
        guidance: str = "",
    ) -> dict[str, Any]:
        """Resolve HITL with the participant-or-admin authority from PR #52."""
        now = store._now_ms()
        with self._transaction() as conn:
            request = conn.execute(
                "SELECT h.*,r.conversation_id,r.assistant_message_id "
                "FROM human_requests h JOIN agent_runs r ON r.id=h.run_id WHERE h.id=?",
                (request_id,),
            ).fetchone()
            if not request:
                raise KeyError(request_id)
            try:
                self._require_participant(
                    conn, actor_id=actor_id, conversation_id=request["conversation_id"]
                )
            except PermissionError:
                if not self._is_admin(conn, actor_id):
                    raise
            allowed = json.loads(request["choices_json"])
            if (
                request["state"] != "waiting"
                or int(request["expires_at_ms"]) < now
                or choice not in allowed
                or not hmac.compare_digest(
                    request["nonce_hash"], self._token_hash(nonce)
                )
            ):
                raise PermissionError(
                    "human request is invalid, expired, or already resolved"
                )
            response = {
                "choice": choice,
                "guidance": store.redact_text(guidance)[:4_000],
                "actor_id": actor_id,
            }
            conn.execute(
                "UPDATE human_requests SET state='resolved',response_json=?,resolved_at_ms=? "
                "WHERE id=? AND state='waiting'",
                (store._json(response), now, request_id),
            )
            terminal = choice.lower().startswith(("reject", "deny", "cancel"))
            target = "cancelled" if terminal else "queued"
            conn.execute(
                "UPDATE agent_runs SET state=?,state_version=state_version+1,updated_at_ms=? "
                "WHERE id=? AND state='waiting_for_human'",
                (target, now, request["run_id"]),
            )
            conn.execute(
                "UPDATE messages SET status=?,updated_at_ms=?,version=version+1 WHERE id=?",
                (target, now, request["assistant_message_id"]),
            )
            scope = json.loads(request["scope_json"])
            actions = scope.get("actions") if isinstance(scope, dict) else []
            if not isinstance(actions, list):
                actions = []
            action_rows = [action for action in actions if isinstance(action, dict)]
            action_names = [str(action.get("name") or "unknown") for action in action_rows]
            self._append_event(
                conn,
                run_id=request["run_id"],
                kind="human_request.resolved",
                payload={
                    "human_request_id": request_id,
                    "request_id": request_id,
                    "choice": choice,
                    "resolution": "rejected" if terminal else "approved",
                    "tool_name": action_names[0] if len(action_names) == 1 else "multiple_tools",
                    "args": {"actions": action_rows},
                    "guidance": response["guidance"],
                },
                actor_id=actor_id,
            )
            self._audit(
                conn,
                actor_id=actor_id,
                action="human_request.resolved",
                resource_type="human_request",
                resource_id=request_id,
                outcome="success",
                metadata={"choice": choice},
            )
            return {
                "id": request_id,
                "run_id": request["run_id"],
                "state": target,
                "choice": choice,
                "decision_count": max(1, len(action_rows)),
            }

    def reissue_human_decision_nonce(
        self: Any, *, actor_id: str, request_id: str
    ) -> dict[str, Any]:
        """Reissue a HITL nonce for an authorized participant or administrator."""
        now = store._now_ms()
        nonce = store.secrets.token_urlsafe(32)
        with self._transaction() as conn:
            request = conn.execute(
                "SELECT h.*,r.conversation_id FROM human_requests h "
                "JOIN agent_runs r ON r.id=h.run_id WHERE h.id=?",
                (request_id,),
            ).fetchone()
            if not request:
                raise KeyError(request_id)
            try:
                self._require_participant(
                    conn, actor_id=actor_id, conversation_id=request["conversation_id"]
                )
            except PermissionError:
                if not self._is_admin(conn, actor_id):
                    raise
            if request["state"] != "waiting" or int(request["expires_at_ms"]) < now:
                raise PermissionError("human request is not awaiting a decision")
            conn.execute(
                "UPDATE human_requests SET nonce_hash=? WHERE id=? AND state='waiting'",
                (self._token_hash(nonce), request_id),
            )
            self._audit(
                conn,
                actor_id=actor_id,
                action="human_request.nonce_reissued",
                resource_type="human_request",
                resource_id=request_id,
                outcome="success",
            )
        return {"id": request_id, "nonce": nonce}

    def munin_add_conversation_participant(
        self: Any, *, conversation_id: str, user_id: str, role: str = "member"
    ) -> dict[str, Any]:
        result = self._durable.add_conversation_participant(
            conversation_id=conversation_id, user_id=user_id, role=role
        )
        self._mirror_participant(
            conversation_id=conversation_id, user_id=user_id, role=role
        )
        return result

    def munin_list_pending_human_requests(
        self: Any, *, actor_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        # Preserve the exact post-fork routing from main/PR #52.
        return self._durable.list_pending_human_requests(
            actor_id=actor_id, limit=limit
        )

    def munin_get_artifact(
        self: Any, *, actor_id: str, artifact_id: str
    ) -> dict[str, Any]:
        with self._durable._read_only() as conn:
            artifact = conn.execute(
                "SELECT conversation_id FROM conversation_artifacts WHERE id=?",
                (artifact_id,),
            ).fetchone()
        if artifact:
            self._hydrate_hot_participant(
                actor_id=actor_id,
                conversation_id=str(artifact["conversation_id"]),
            )
        return self._durable.get_artifact(actor_id=actor_id, artifact_id=artifact_id)

    def active_run_for_conversation(
        self: Any, *, conversation_id: str
    ) -> dict[str, Any] | None:
        """Return the newest live run so Discord text becomes guidance, not a new turn."""
        backends = (
            (self._hot, self._durable)
            if self._durable is not self._hot
            else (self._hot,)
        )
        for backend in backends:
            try:
                with backend._read_only() as conn:
                    row = conn.execute(
                        "SELECT * FROM agent_runs WHERE conversation_id=? AND state IN "
                        "('queued','running','waiting_for_human') "
                        "ORDER BY created_at_ms DESC LIMIT 1",
                        (conversation_id,),
                    ).fetchone()
                    if row is not None:
                        return ProductionStore._run_dict(row)
            except Exception:  # noqa: BLE001 - hot/durable fallback is intentional
                continue
        return None

    ProductionStore.add_conversation_participant = add_conversation_participant
    ProductionStore.list_pending_human_requests = list_pending_human_requests
    ProductionStore.resolve_human_decision = resolve_human_decision
    ProductionStore.reissue_human_decision_nonce = reissue_human_decision_nonce

    MuninStore.add_conversation_participant = munin_add_conversation_participant
    MuninStore.list_pending_human_requests = munin_list_pending_human_requests
    MuninStore.get_artifact = munin_get_artifact
    MuninStore.active_run_for_conversation = active_run_for_conversation

    store._UNIFIED_STORE_RECONCILED = True
