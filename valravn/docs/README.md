# Valravn - Documentation

This folder holds historical specs, plans and reviews for the development of
the Valravn mesh's Burp DAST surface. High-level docs (README, AGENTS, CLAUDE,
NOTICE, skill.json) live in `valravn/` (subtree root); per-date design
documents live here.

## Structure

```
docs/
├── plans/       Implementation plans per milestone
├── specs/       Design specs (incl. agent council, KB refresh, autonomy frontier)
├── reviews/     Gap reviews vs. competition / external releases
└── *.md         One-off docs (gap analysis, agent status schema)
```

## Convention

Files are date-prefixed (`YYYY-MM-DD-...`) for natural chronological order.
The original body language is preserved except where a rework is noted - the
docs are snapshots of design decisions; code is truth. For behavioral contracts
see `../CLAUDE.md` and `../AGENTS.md`. For contribution rules see
`../README.md#contributing`.

## Index by milestone

### Plans

- `plans/2026-05-21-large-file-split-and-gap-fixes.md` - Plan: large-file split
  + gap fixes. Implementation of scope-relax and KB expansion.
- `plans/2026-05-21-scope-relax-and-kb-expansion.md` - Plan: scope-relax +
  smart fuzzing + KB expansion in 2026 H2.
- `plans/2026-05-22-grow-agent.md` - Plan: grow-agent, the per-domain
  orchestrator.
- `plans/2026-07-20-competitive-gap-closure.md` - Plan: gap-closure roadmap
  (W33+).
- `plans/2026-07-23-spec-D-p0-kb-refresh.md` - Plan: Spec D P0 - KB refresh
  2026 H2.

### Specs

- `specs/2026-05-21-large-file-split-and-gap-fixes-design.md` - Design spec for
  the large-file-split plan.
- `specs/2026-05-21-scope-relax-and-kb-expansion-design.md` - Design spec for
  scope-relax + smart fuzz + novel KB.
- `specs/2026-05-22-grow-agent-design.md` - Design spec for grow-agent.
- `specs/2026-05-24-praetor-v1-milestone.md` - v1.0 milestone plan (Praetor =
  internal codename of the Burp extension).
- `specs/2026-07-21-agent-council-design.md` - Spec 1: agent council +
  Real-Eyes/Handoff.
- `specs/2026-07-23-enhancement-roadmap.md` - Roadmap 2026-07-23.
- `specs/2026-07-23-spec-D-kb-refresh-2026h2.md` - Spec D: KB/payload/technique
  refresh 2026 H2.
- `specs/2026-07-23-spec-E-token-and-agent-efficiency.md` - Spec E: token &
  agent-team efficiency.
- `specs/2026-07-23-spec-F-autonomy-frontier.md` - Spec F: autonomy frontier.

### Reviews

- `reviews/2026-07-25-cloud-and-burp-ai-at-gap-review.md` - Review: cloud
  pentest / red-team + Burp AI / Burp AT gap (2026-07-25).

### Standalone

- `agent-status-schema.md` - Schema of the agent status object.
- `2026-08-02-competitive-gap-analysis.md` - Gap analysis verified against the
  codebase (2026-08-02).
