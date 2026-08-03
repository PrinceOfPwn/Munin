# Local documentation audit

Munin's documentation audit uses the official `Qwen/Qwen3-4B-GGUF` Q4_K_M model on a CPU-only GitHub-hosted runner. It performs two local inference stages:

1. Each selected source file is supplied to the model in full. The result is structured JSON written only in English.
2. A separate aggregation inference deduplicates the file reports and generates an extensive pull-request section in English and Spanish.

The model is not an agent. It has no shell, filesystem tools, network tools, or patch capability. Repository files are opened as text, never imported or executed. A final `git diff --exit-code` check verifies that tracked files were not changed.

## Runs

- The first successful push to `main` without a baseline marker scans the complete repository and stores a cache marker only after the read-only checks pass.
- Later pushes to `main` analyze every supported source file changed by the merge.
- Pull-request runs analyze every changed supported source file, while still sending each whole file to the model.
- `workflow_dispatch` can force either a full scan or an explicitly selected commit range.

If the baseline cache is eventually evicted, the next `main` push safely recreates the full baseline. A file that cannot fit into the pinned 32,768-token context is reported as skipped and is never silently truncated.

## Imported symbols and external context

The file reviewer does not receive the implementations of imported symbols unless they happen to be present in the same file. Its prompt therefore forbids assumptions about external behavior. Findings that require another implementation must use `requires_external_context`; they are always non-blocking.

## Security boundary

The analysis workflow has `contents: read` only. A second `workflow_run` workflow, loaded from the trusted default branch, downloads the completed Markdown artifact and replaces only the section bounded by:

```text
<!-- munin-doc-audit:start -->
<!-- munin-doc-audit:end -->
```

This avoids granting a pull-request branch a write-capable token. The publisher does not execute artifact content and rejects oversized or unbounded reports.

## Configuration

`.munin-doc-audit.toml` pins the official model revision, quantization, context size, confidence threshold, ignored paths, and output budgets. The model weights are cached by GitHub Actions and loaded once per audit job.

The full per-file JSON, manifest, selected-file list, bilingual Markdown, and job summary are retained as a workflow artifact for 90 days.
