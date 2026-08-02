"""Declarative Red Team agent profiles and a safe Shadow Council contract.

Profiles are data, not prompt text.  The dispatcher resolves tools, models,
scope and escalation from this module before an agent can run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Risk = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class AgentProfile:
    id: str
    role: str
    objective: str
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    input_contract: str
    output_contract: str
    budget_tokens: int
    timeout_seconds: int
    risk: Risk
    scope_policy: str
    escalation: str
    completion_criteria: str
    artifacts: tuple[str, ...]
    hitl_required: bool
    retries: int
    preferred_model_use: str
    fallback_model_use: str


_COMMON = dict(
    input_contract="objective, authorized scope, evidence references, context manifest",
    output_contract="redacted operational summary, cited evidence, tool events, artifacts, blockers",
    budget_tokens=18_000,
    timeout_seconds=900,
    scope_policy="deny outside the operation scope; record every tool intent",
    escalation="pause for HITL when scope, impact, or authorization is ambiguous",
    artifacts=("markdown", "json"),
    retries=1,
    fallback_model_use="conversation",
)


def _profile(id: str, role: str, objective: str, tools: tuple[str, ...], skills: tuple[str, ...], risk: Risk, completion: str, *, hitl: bool = False, model: str = "coordination") -> AgentProfile:
    return AgentProfile(id=id, role=role, objective=objective, tools=tools, skills=skills, risk=risk, completion_criteria=completion, hitl_required=hitl, preferred_model_use=model, **_COMMON)


DEFAULT_AGENT_PROFILES: tuple[AgentProfile, ...] = (
    _profile("recon-coordinator", "Recon Coordinator", "Decompose the authorized objective, coordinate evidence collection, and maintain the operation plan.", ("scope_check", "delegate", "evidence_index"), ("recon", "opsec"), "medium", "Plan is versioned, evidence is attributed, and all blockers are visible."),
    _profile("web-specialist", "Web Specialist", "Assess authorized web exposure and validate findings without exceeding the web scope.", ("http_probe", "browser_capture", "evidence_index"), ("web", "owasp"), "high", "Each finding has reproducible, redacted evidence.", hitl=True),
    _profile("ad-ldap-specialist", "AD/LDAP Specialist", "Analyze authorized directory posture and privilege paths.", ("ldap_query", "graph_query", "evidence_index"), ("active-directory", "ldap"), "high", "Directory findings are scope-bound and evidence-cited.", hitl=True),
    _profile("cloud-specialist", "Cloud Specialist", "Evaluate authorized cloud identity, configuration, and attack paths.", ("cloud_inventory", "policy_analyzer", "evidence_index"), ("cloud", "iam"), "high", "Cloud findings include account and scope provenance.", hitl=True),
    _profile("source-sast-specialist", "Source/SAST Specialist", "Inspect source and repositories for verifiable security findings.", ("repo_search", "sast_scan", "artifact_write"), ("source-review", "sast"), "medium", "Code references, paths, and remediation evidence are attached."),
    _profile("vulnerability-validation", "Vulnerability Validation Specialist", "Safely validate a reported weakness only within an approved engagement.", ("scope_check", "validation_probe", "evidence_index"), ("validation", "opsec"), "critical", "Validation is repeatable without destructive impact.", hitl=True),
    _profile("python-automation", "Python Automation Engineer", "Build redacted, reviewable automation artifacts for approved tasks.", ("artifact_write", "sandbox_test", "lint"), ("python", "automation"), "medium", "Artifact is tested, scoped, and downloadable."),
    _profile("tool-forge", "Tool Forge Engineer", "Propose and test controlled tool extensions without silently changing production.", ("proposal_diff", "sandbox_test", "typecheck"), ("tool-forge", "extensions"), "high", "Human-approved diff passes isolated validation.", hitl=True, model="tool_forge"),
    _profile("graph-engineer", "Graph Engineer", "Connect evidence, assets, findings and provenance in the intelligence graph.", ("graph_query", "graph_write", "evidence_index"), ("graph", "entities"), "medium", "Graph changes carry source IDs and confidence."),
    _profile("hugin-librarian", "Hugin Librarian", "Retrieve and validate the smallest relevant Hugin evidence set for the active subtask.", ("hugin_rag_search", "hugin_plan_for", "hugin_node_detail", "hugin_neighbors", "evidence_index"), ("hugin-research",), "low", "Relevant Hugin node IDs and source URLs are attached to a bounded evidence summary."),
    _profile("evidence-reporting", "Evidence/Reporting Specialist", "Turn durable evidence into a concise, provenance-preserving report.", ("artifact_write", "evidence_index", "export"), ("reporting", "evidence"), "low", "Report citations resolve to durable events and artifacts."),
    _profile("opsec-scope-guardian", "OPSEC/Scope Guardian", "Enforce scope, permission and OPSEC boundaries before high-impact work.", ("scope_check", "human_request", "audit"), ("opsec", "governance"), "critical", "Unauthorized or ambiguous action is paused and audited.", hitl=True),
)


def profile_catalog() -> list[dict[str, object]]:
    """JSON-safe catalog for API and UI roster views."""
    return [asdict(profile) for profile in DEFAULT_AGENT_PROFILES]


def shadow_council_resolution(votes: list[dict[str, object]]) -> dict[str, object]:
    """Summarize votes; this never grants scope or consumes a human approval."""
    normalized = [vote for vote in votes if isinstance(vote.get("recommendation"), str)]
    by_recommendation: dict[str, int] = {}
    for vote in normalized:
        recommendation = str(vote["recommendation"])
        by_recommendation[recommendation] = by_recommendation.get(recommendation, 0) + 1
    consensus = max(by_recommendation, key=by_recommendation.get) if by_recommendation else None
    return {
        "votes": normalized,
        "recommendation": consensus,
        "agreement_count": by_recommendation.get(consensus, 0) if consensus else 0,
        "dissent": [vote for vote in normalized if vote["recommendation"] != consensus],
        "requires_human_authorization": True,
        "note": "Shadow Council informs the coordinator; it cannot bypass HITL, permissions, or scope.",
    }
