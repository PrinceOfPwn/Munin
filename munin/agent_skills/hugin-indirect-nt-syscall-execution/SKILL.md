---
name: hugin-indirect-nt-syscall-execution
description: "Indirect NT Syscall Execution — Rust + Red Team low-level tradecraft extracted from the Hugin knowledge graph. Category: defense_evasion. MITRE: . Tier: . Tags: technique, asm. Use when implementing or analyzing this technique in Rust, designing C2/implant components, or studying Windows internals for offensive security."
---

## Indirect NT Syscall Execution

Execution of NT syscalls by loading an SSN into eax and jumping to a syscall instruction address retrieved from a global variable.

---

Derived automatically from evidence-grounded enrichment of **indirect.asm**.

## Cross-References (Hugin graph)

**Source:** Hugin graph node `technique:f0c43c35fc43b76b17e5a2` (file: `n/a`, evidence: `SYN-BED4507A7DFD`)
