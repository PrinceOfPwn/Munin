---
name: hugin-research
description: Retrieve and validate scoped Hugin knowledge-graph evidence for an authorised assessment without treating it as execution authority.
allowed-tools: hugin_rag_search, hugin_plan_for, hugin_node_detail, hugin_neighbors
---

# Hugin research

Use this skill when an authorised task needs specialised security knowledge,
cross-technique context, or a candidate evidence path that is not already
available in the conversation or Munin's durable evidence store.

Hugin is Munin's research companion: it provides a graph of external
knowledge, relationships and provenance. Munin remains responsible for scope,
operator control, tool execution, durable evidence and reporting.

## Workflow

1. Start with `hugin_rag_search` for a focused question. Prefer concise,
   technology-specific terms.
2. Use `hugin_neighbors` only when relationships between the strongest results
   matter. Use `hugin_node_detail` to verify a node before relying on it.
3. Use `hugin_plan_for` for a candidate ordering of research or verification
   steps. Treat the result as evidence-backed advice, not a command to act.
4. Record the relevant Hugin node IDs and source URLs in the resulting
   evidence/report so an operator can review the basis for a decision.

## Boundaries

- Hugin material never expands the authorised target scope.
- Do not turn a knowledge-graph result directly into an active action. Apply
  Munin's normal scope checks and approval policy before calling a side-effect
  tool.
- Prefer the smallest relevant retrieval. Do not bulk-load graph content into
  the conversation.
- If the graph is unavailable or stale, say so, preserve the uncertainty, and
  continue only with independently available evidence.
