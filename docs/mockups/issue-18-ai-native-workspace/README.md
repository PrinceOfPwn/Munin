# Munin Issue #18 — HTML/CSS reference

This directory contains a static, responsive visual reference for the AI-native frontend workspace described in issue #18.

## Open locally

```bash
cd docs/mockups/issue-18-ai-native-workspace
python -m http.server 4173
# open http://127.0.0.1:4173
```

The file is intentionally standalone: the CSS is embedded in `index.html`, it has no runtime dependencies, and it does not call Munin APIs.

## What the reference defines

- Four visual zones: primary rail, operations list, conversation/execution, and contextual workspace.
- Final answers are visually stronger than low-level telemetry.
- Tool calls are grouped by a stable tool call identity.
- Command output is contained inside its own horizontally scrollable/wrappable terminal surface.
- Subagents are summarized rather than flooding the message timeline.
- Artifacts are first-class objects with actions, tabs, IOC tables, and provenance.
- `Guide`, `Send`, `Detach`, and `Cancel run` are distinct actions.
- Detach is a viewer action; cancellation is a durable backend mutation.
- The workspace has separate Artifact, Evidence, Run, and Agents views.

## Production implementation

The production UI must remain React/Next.js and use the existing AI SDK UI transport. This file is not intended to replace `AppShell`, `AgentConsole`, or the BFF.

Target flow:

```text
Python/LangGraph runtime
  -> authenticated Munin SSE envelopes
  -> Next.js /api/chat BFF
  -> AI SDK UIMessageChunk stream
  -> typed UIMessage parts
  -> AI Elements primitives + Munin-specific renderers
```

## AI Elements compatibility warning

The current repository uses React 18 and Tailwind CSS 3. Current AI Elements setup documentation lists React 19 and Tailwind CSS 4 as prerequisites.

Do not run the installer blindly. Choose one reviewed strategy:

1. Upgrade React/Tailwind/shadcn first, then install AI Elements normally; or
2. Vendor/adapt only selected AI Elements source files to the current stack.

For the first redesign PR, adapting selected primitives is likely lower-risk than combining a framework upgrade with the product redesign.

## Security boundary for generative UI

Never render arbitrary JSX or JavaScript emitted by the model.

```text
backend result / event
  -> versioned Zod schema
  -> allow-listed renderer key
  -> trusted Munin React component
```

Generated HTML must be treated as an untrusted artifact and rendered only in a hardened sandboxed iframe with no same-origin access, cookies, local storage, parent DOM access, or network access by default.
