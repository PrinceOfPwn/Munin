# LLM provider contract

Munin supports managed provider profiles and an environment fallback through an
OpenAI-compatible endpoint. Provider selection changes model behaviour; it does
not change Munin's server-side policy, capability checks or HITL protocol.

## Configure a provider

Set the endpoint, model and credentials in `.env` for the environment fallback,
or create an authenticated provider profile in the web settings. Keep API keys
on the backend: the browser sends a profile to the authenticated server and
does not persist the secret in its local query cache.

Every provider used for operational work should be tested for normal text,
structured tool calls, streaming, timeout behaviour and the intended model.

## Required behaviour

A compatible provider must be able to:

- return normal assistant content;
- support the LangChain message and tool-call path used by the runtime;
- stream deltas when the UI needs live progress; and
- make errors and timeouts distinguishable from a completed answer.

The capability registry is supplied by Munin. A provider cannot make a tool
available by describing one in natural language.

## Provider-emitted reasoning

Some providers emit explicit fields such as `reasoning_content`, `reasoning`,
`thinking` or `<think>`-tagged output. Munin keeps these deltas separate from
final assistant text, persists the corresponding timeline event and restores it
through replay.

This is intentionally limited to content the provider emits. Munin does not
derive or reconstruct private reasoning from graph internals, tool calls or
operational logs. Providers without an explicit reasoning channel simply have
no reasoning part in the UI.

## Timeouts and long-running work

Tune model request timeouts for the provider's real latency, while keeping run
leases, cancellation and tool/HITL policy as independent controls. A slow model
must not cause the client to submit a second identical turn: the conversation
stream can be replayed while the server-owned run continues.

## Acceptance checklist

Before enabling a provider profile:

1. verify the endpoint and model identity;
2. complete a streaming text response;
3. complete a structured tool-call round trip in an isolated fixture;
4. verify explicit reasoning handling if the provider offers it;
5. test a timeout or provider error path; and
6. confirm credentials never appear in browser storage, logs or artifacts.
