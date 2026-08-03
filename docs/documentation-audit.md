# Agentic local documentation audit

Munin's documentation audit reviews selected source files without changing repository code. The diff chooses which primary files are reviewed; each selected primary file is always supplied in full.

The workflow has three bounded stages:

1. A provider router tries the configured GitHub Models catalog and inference endpoint once.
2. A small LangGraph decides whether a primary file genuinely needs context from a local dependency.
3. File-level English JSON reports are consolidated into one extensive pull-request section in English and Spanish.

## Provider order

GitHub retired the public GitHub Models playground, catalog, and inference API on July 30, 2026. The probe remains for compatibility with GitHub Enterprise installations or a future replacement endpoint, but public GitHub is expected to reject it and immediately use the local fallback.

When a compatible catalog is available, the router searches the configured preference order. The order is informed by the August 2026 Artificial Analysis intelligence leaderboard:

1. Claude Opus 5
2. GPT-5.6 Sol
3. Kimi K3
4. GLM-5.2
5. MiniMax-M3
6. Qwen3.6 27B

Availability in a provider catalog is still required. A benchmark ranking does not grant access to a model.

The CPU fallback is `bartowski/Qwen_Qwen3.5-4B-GGUF`, file `Qwen_Qwen3.5-4B-Q4_K_M.gguf`, pinned by repository revision and SHA256. Artificial Analysis scores Qwen3.5 4B substantially above the deprecated Qwen3 4B, while its approximately 4.7-billion-parameter size remains practical for a standard GitHub-hosted CPU runner. The upstream Qwen organization publishes the base model; the pinned GGUF quantization is a community conversion, so the workflow verifies its complete SHA256 before loading it.

## Bounded LangGraph

The graph is deliberately small:

```text
primary file
    |
    v
resolve local import candidates
    |
    v
model selects zero to three exact paths
    |                         \
    | no context               \ context requested
    v                           v
review primary file        read allowlisted files in full
                                |
                                v
                         review primary file
```

The model does not receive a general file-reading tool. Candidate paths are resolved deterministically from tracked local imports:

- Python imports, including relative imports.
- Relative JavaScript and TypeScript imports or `require()` calls.
- Quoted C and C++ includes.

The model can choose only exact paths from that allowlist. It gets one selection round, may request at most three files, and cannot follow transitive imports. Related files are read completely or skipped; they are never silently truncated.

Related files are auxiliary evidence only. Findings continue to target the primary file and its line numbers.

## Security boundary

The analyzer has no shell tool, arbitrary path tool, repository-write tool, code execution tool, or recursive browsing loop. Source files are opened as text and are never imported or executed.

The analysis workflow has:

- `contents: read`
- `models: read`, retained only for the compatibility probe

A final `git diff --exit-code` verifies that tracked files were not modified.

A second trusted `workflow_run` workflow downloads the bounded Markdown artifact and replaces only the pull-request section between:

```text
<!-- munin-doc-audit:start -->
<!-- munin-doc-audit:end -->
```

The pull-request branch never receives a write-capable token.

## Full and incremental runs

- The first successful push to `main` without the version-two baseline marker scans the complete repository.
- Later pushes to `main` select supported files changed by the merge.
- Pull-request runs select supported files changed between the base and head commits.
- Every selected primary file is still reviewed in full.
- `workflow_dispatch` can force a complete scan or a chosen commit range.
- If the baseline cache is evicted, the next push to `main` safely creates a new full baseline.

## Outputs

The workflow retains for 90 days:

- one English JSON report per primary file;
- the list of selected primary files;
- context candidates, requested paths, loaded paths, and skipped paths in report metadata;
- a manifest containing the final provider;
- a job summary;
- the bounded bilingual pull-request Markdown.

The model is loaded once per job and reused for context planning, file reviews, and final aggregation.
