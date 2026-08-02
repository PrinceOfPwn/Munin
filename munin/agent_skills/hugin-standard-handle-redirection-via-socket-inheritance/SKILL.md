---
name: hugin-standard-handle-redirection-via-socket-inheritance
description: "Standard Handle Redirection via Socket Inheritance — Rust + Red Team low-level tradecraft extracted from the Hugin knowledge graph. Category: execution. MITRE: . Tier: . Tags: technique, asm. Use when implementing or analyzing this technique in Rust, designing C2/implant components, or studying Windows internals for offensive security."
---

## Standard Handle Redirection via Socket Inheritance

Redirecting a spawned process's standard input, output, and error streams to a network socket by marking it inheritable and mapping it in STARTUPINFOA.

---

Derived automatically from evidence-grounded enrichment of **rev.asm**.

## Cross-References (Hugin graph)

**Source:** Hugin graph node `technique:d451f42caa846a81f649dc` (file: `n/a`, evidence: `SYN-018F932A1434`)
