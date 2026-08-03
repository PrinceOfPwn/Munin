# Valravn — Documentación

Esta carpeta contiene specs, plans y reviews históricos del desarrollo de la
surface Burp DAST de la mesh Valravn. Los docs altos (README, AGENTS, CLAUDE,
NOTICE, skill.json) están en `valravn/` (raíz del subtree); acá viven los
documentos de diseño por fecha.

## Estructura

```
docs/
├── plans/       Implementation plans por milestone
├── specs/       Design specs (incl. agent council, KB refresh, autonomy frontier)
├── reviews/     Gap reviews vs. competencia / releases externos
└── *.md         Docs puntuales (gap analysis, agent status schema)
```

## Convención

Los archivos están date-prefixed (`YYYY-MM-DD-…`) para orden cronológico natural.
Lenguaje original del cuerpo preservado salvo donde se anota reforma — los docs
son snapshots de decisiones de diseño; el código es verdad. Para behavioral
contracts ver `.. /CLAUDE.md` y `../AGENTS.md`. Para contribution rules ver
`../README.md#contribuir`.

## Índice por milestone

### Plans

- `plans/2026-05-21-large-file-split-and-gap-fixes.md` — Plan: split de archivos
  large + gap fixes. Implementación de scope-relax y KB expansion.
- `plans/2026-05-21-scope-relax-and-kb-expansion.md` — Plan: scope-relax +
  smart fuzzing + KB expansion en 2026 H2.
- `plans/2026-05-22-grow-agent.md` — Plan: grow-agent, el orchestrator
  per-domain.
- `plans/2026-07-20-competitive-gap-closure.md` — Plan: roadmap de gap-closure
  (W33+).
- `plans/2026-07-23-spec-D-p0-kb-refresh.md` — Plan: Spec D P0 — refresh KB
  2026 H2.

### Specs

- `specs/2026-05-21-large-file-split-and-gap-fixes-design.md` — Design spec del
  plan large-file-split.
- `specs/2026-05-21-scope-relax-and-kb-expansion-design.md` — Design spec scope
  relax + smart fuzz + novel KB.
- `specs/2026-05-22-grow-agent-design.md` — Design spec grow-agent.
- `specs/2026-05-24-praetor-v1-milestone.md` — Plan v1.0 milestone (Praetor =
  internal codename de la Burp extension).
- `specs/2026-07-21-agent-council-design.md` — Spec 1: agent council +
  Real-Eyes/Handoff.
- `specs/2026-07-23-enhancement-roadmap.md` — Roadmap 2026-07-23.
- `specs/2026-07-23-spec-D-kb-refresh-2026h2.md` — Spec D: KB/payload/technique
  refresh 2026 H2.
- `specs/2026-07-23-spec-E-token-and-agent-efficiency.md` — Spec E: token &
  agent-team efficiency.
- `specs/2026-07-23-spec-F-autonomy-frontier.md` — Spec F: autonomy frontier.

### Reviews

- `reviews/2026-07-25-cloud-and-burp-ai-at-gap-review.md` — Review: cloud
  pentest / red-team + Burp AI / Burp AT gap (2026-07-25).

### Sueltos

- `agent-status-schema.md` — Schema del agent status object.
- `2026-08-02-competitive-gap-analysis.md` — Gap analysis verificado contra
  codebase (2026-08-02).
