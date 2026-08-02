# Operator guide

This guide covers authorized operation of Munin v1.0.0.

## Verified configuration

The tested configuration is **Web GUI + GitHub Actions + MiMo V2.5**. Other
models and deployment targets may work, but should be treated as experimental
until a full structured tool-call, streaming, replay and recovery loop passes.

## Before starting

- Confirm written authorization and target boundaries.
- Verify `/health`, authentication and allowed origins.
- Test one complete structured tool-call round trip.
- Inspect the live capability registry.
- Persist the hot database and checkpoint database.
- Define who can approve, reject and cancel sensitive actions.
- Choose an appropriate Soul; the bundled CTF profile is not the production default.

## During an operation

Follow the durable event timeline rather than relying only on final model text.
Review exact capability names and arguments before approval. Stop or cancel when
scope changes, routing becomes unsafe, credentials are exposed or evidence
contradicts the expected environment.

## Recovery

After process loss, confirm lease expiry, checkpoint availability and replayed
events before allowing further execution. Pending human requests must remain
paused and completed tool calls must not repeat.

## Production deployment

Use durable volumes, protected ingress, strong authentication, strict origins,
secret management, retention policies and explicit backup/recovery tests.
GitHub Actions is the verified v1.0.0 runner path, but its filesystem is
ephemeral unless state is exported or stored remotely.

## Evidence and reporting

Keep provider attribution, retrieval time, target, tool output and artifacts.
Separate confirmed facts, inferences and unknowns. A successful tool call is not
a substitute for evidence-backed mission success.
