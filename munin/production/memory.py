# tags: [memory, episodic-memory, shared-intel, coordination, core, runtime, orchestrator, ContextCandidate, select_context, should_compact, summary_provenance_payload, token-budgeting, source-provenance, context-compaction, candidate-scoring]
"""Context selection and compaction scheduling with source provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextCandidate:
    id: str
    kind: str
    content: str
    token_estimate: int
    recency: float
    relevance: float
    importance: float
    scope_match: float
    provenance: tuple[str, ...]

    @property
    def score(self) -> float:
        return self.recency * 0.27 + self.relevance * 0.36 + self.importance * 0.22 + self.scope_match * 0.15


def select_context(candidates: list[ContextCandidate], *, token_budget: int) -> list[ContextCandidate]:
    """Deterministic budgeted selection; summaries never replace raw provenance."""
    selected: list[ContextCandidate] = []
    used = 0
    for candidate in sorted(candidates, key=lambda item: (item.score, item.id), reverse=True):
        if candidate.token_estimate <= 0 or used + candidate.token_estimate > token_budget:
            continue
        selected.append(candidate)
        used += candidate.token_estimate
    return selected


def should_compact(*, event_count: int, token_estimate: int, event_threshold: int = 24, token_budget: int = 12_000, conversation_closed: bool = False, requested: bool = False) -> bool:
    return requested or conversation_closed or event_count >= event_threshold or token_estimate >= token_budget


def summary_provenance_payload(*, source_ids: list[str], source_hash: str, model: str, prompt_version: str, findings: list[str], decisions: list[str], open_tasks: list[str]) -> dict[str, Any]:
    return {"source_ids": source_ids, "source_hash": source_hash, "model": model, "prompt_version": prompt_version, "findings": findings, "decisions": decisions, "open_tasks": open_tasks}
