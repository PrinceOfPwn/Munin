"""Durable Discord adapter for the same conversation/run aggregate as the UI."""

from __future__ import annotations

import os
import threading
from typing import Any

from .dispatcher import ProductionDispatcher
from .store import ProductionStore


class DurableDiscordAdapter:
    """Queues an allowlisted Discord prompt; it never sends a provider key to Discord."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.actor_id = os.environ.get("MUNIN_DISCORD_DURABLE_ACTOR_ID", "").strip()
        self.conversation_id = os.environ.get("MUNIN_DISCORD_CONVERSATION_ID", "").strip()
        if not self.actor_id or not settings.db_url.startswith(("libsql://", "libsqls://")):
            raise RuntimeError("durable Discord requires MUNIN_DISCORD_DURABLE_ACTOR_ID and authoritative Turso")
        self.store = ProductionStore.for_settings(settings, master_key=ProductionStore.master_key_from_environment())

    def enqueue(self, *, author_id: int, author: str, prompt: str) -> dict[str, str]:
        conversation_id = self.conversation_id
        if not conversation_id:
            conversation = self.store.create_conversation(owner_id=self.actor_id, title=f"Discord / {author}"[:160], tags=["discord", str(author_id)], scope={"source": "discord", "author_id": author_id})
            conversation_id = conversation["id"]
        result = self.store.create_turn(actor_id=self.actor_id, conversation_id=conversation_id, content=prompt, idempotency_key=f"discord:{author_id}:{threading.get_ident()}:{prompt[:32]}")
        if not result["idempotent_replay"]:
            dispatcher = ProductionDispatcher(self.store, self.settings, worker_id=f"discord-{author_id}")
            threading.Thread(target=dispatcher.run_once, name=f"munin-discord-{result['run']['id']}", daemon=True).start()
        return {"conversation_id": conversation_id, "run_id": result["run"]["id"], "state": result["run"]["state"]}
