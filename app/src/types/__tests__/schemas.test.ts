// tags: [tests, munin-ui-schemas, zod, schema-validation, safeParse, PR-2F]
// -----------------------------------------------------------------------------
// munin-ui/v1 schema document tests.
//
// Vitest is the configured test runner for ``app`` (see ``app/package.json``
// ``test`` script = ``vitest run``). These tests document the contract for
// each discriminated schema: known payloads parse, malformed payloads
// degrade instead of crashing, and the union discriminator selects the right
// branch. CI runs them on the GitHub Actions runner that has ``node_modules``.
// -----------------------------------------------------------------------------
import { describe, expect, it } from "vitest";

import {
  artifactSchema,
  commandOutputSchema,
  guidanceLifecycleSchema,
  hitlRequestSchema,
  muninUiV1PartSchema,
  muninUiV1Schemas,
  MUNIN_UI_V1_PART_TYPES,
  operationalTraceSchema,
  planSchema,
  reasoningSchema,
  schemaForV1PartType,
  toolInvocationSchema,
} from "@/types/muninUiSchemas";

describe("munin-ui/v1 schemas", () => {
  it("exports one schema per declared v1 discriminator key", () => {
    for (const key of MUNIN_UI_V1_PART_TYPES) {
      expect(muninUiV1Schemas[key], `missing schema for ${key}`).toBeDefined();
    }
    expect(MUNIN_UI_V1_PART_TYPES).toHaveLength(8);
  });

  it("rejects a payload with an unknown discriminator via the union", () => {
    const parsed = muninUiV1PartSchema.safeParse({ type: "not-a-real-part" });
    expect(parsed.success).toBe(false);
  });

  it("schemaForV1PartType returns null for unknown keys", () => {
    expect(schemaForV1PartType("nope")).toBeNull();
    expect(schemaForV1PartType(123)).toBeNull();
  });

  it("schemaForV1PartType returns the underlying schema for known keys", () => {
    expect(schemaForV1PartType("tool-invocation")).toBe(toolInvocationSchema);
    expect(schemaForV1PartType("guidance-lifecycle")).toBe(guidanceLifecycleSchema);
  });

  describe("tool-invocation", () => {
    it("accepts a partial-call with arbitrary input", () => {
      const parsed = toolInvocationSchema.safeParse({
        type: "tool-invocation",
        toolCallId: "call_1",
        toolName: "recon",
        state: "partial-call",
        input: { target: { addr: "10.0.0.1" } },
      });
      expect(parsed.success).toBe(true);
    });

    it("accepts a terminal result with arbitrary output", () => {
      const parsed = toolInvocationSchema.safeParse({
        type: "tool-invocation",
        toolCallId: "call_2",
        toolName: "exec",
        state: "result",
        result: { ok: true, lines: ["a", "b"] },
      });
      expect(parsed.success).toBe(true);
    });

    it("rejects an unknown state", () => {
      const parsed = toolInvocationSchema.safeParse({
        type: "tool-invocation",
        toolCallId: "call_x",
        toolName: "exec",
        state: "pending",
      });
      expect(parsed.success).toBe(false);
    });
  });

  describe("command-output", () => {
    it("accepts a streaming line", () => {
      const parsed = commandOutputSchema.safeParse({
        type: "command-output",
        toolName: "exec",
        stream: "stdout",
        text: "PING 10.0.0.1",
        sequence: 3,
        final: false,
      });
      expect(parsed.success).toBe(true);
    });

    it("accepts a final aggregate frame", () => {
      const parsed = commandOutputSchema.safeParse({
        type: "command-output",
        toolName: "whoami",
        text: "operator",
        final: true,
      });
      expect(parsed.success).toBe(true);
    });

    it("rejects a bogus stream token", () => {
      const parsed = commandOutputSchema.safeParse({
        type: "command-output",
        toolName: "exec",
        stream: "panic",
        text: "x",
      });
      expect(parsed.success).toBe(false);
    });
  });

  describe("operational-trace", () => {
    it("accepts a stage + text bundle", () => {
      const parsed = operationalTraceSchema.safeParse({
        type: "operational-trace",
        stage: "recon",
        text: "port scan complete",
      });
      expect(parsed.success).toBe(true);
    });

    it("rejects a missing stage", () => {
      const parsed = operationalTraceSchema.safeParse({
        type: "operational-trace",
        text: "no stage",
      });
      expect(parsed.success).toBe(false);
    });
  });

  describe("hitl-request", () => {
    it("accepts a pending card with arbitrary args", () => {
      const parsed = hitlRequestSchema.safeParse({
        type: "hitl-request",
        requestId: "req_1",
        toolName: "authorize-destructive-op",
        args: { target: { actions: [{ name: "drop-table" }] } },
        nonce: "abc",
        choices: ["approve", "reject"],
        resolved: false,
      });
      expect(parsed.success).toBe(true);
    });

    it("accepts a resolution-only card", () => {
      const parsed = hitlRequestSchema.safeParse({
        type: "hitl-request",
        requestId: "req_2",
        resolved: true,
        resolution: "rejected",
      });
      expect(parsed.success).toBe(true);
    });

    it("rejects an unknown resolution value", () => {
      const parsed = hitlRequestSchema.safeParse({
        type: "hitl-request",
        requestId: "req_3",
        resolved: true,
        resolution: "deferred",
      });
      expect(parsed.success).toBe(false);
    });
  });

  describe("artifact", () => {
    it("accepts an artifact pointer", () => {
      const parsed = artifactSchema.safeParse({
        type: "artifact",
        artifactId: "art_1",
        mimeType: "application/pdf",
        uri: "https://example.test/art_1.pdf",
      });
      expect(parsed.success).toBe(true);
    });

    it("rejects a missing artifactId", () => {
      const parsed = artifactSchema.safeParse({ type: "artifact" });
      expect(parsed.success).toBe(false);
    });
  });

  describe("reasoning", () => {
    it("accepts a terminal reasoning part", () => {
      const parsed = reasoningSchema.safeParse({
        type: "reasoning",
        id: "reasoning-r1",
        text: "host is likely a domain controller",
        step: 2,
        provider: "openai-compatible",
      });
      expect(parsed.success).toBe(true);
    });

    it("accepts a streaming delta", () => {
      const parsed = reasoningSchema.safeParse({
        type: "reasoning",
        id: "reasoning-r2",
        delta: "host",
      });
      expect(parsed.success).toBe(true);
    });
  });

  describe("plan", () => {
    it("accepts a full plan snapshot", () => {
      const parsed = planSchema.safeParse({
        type: "plan",
        goal: {
          id: "goal_1",
          objective: "enumerate domain hosts",
          state: "active",
          successCriteria: ["at least 5 hosts found"],
          deadlineMs: null,
        },
        items: [
          {
            id: "i1",
            title: "nmap sweep",
            status: "done",
            owner: "agent",
            updatedAtMs: 1234,
          },
          {
            id: "i2",
            title: "Kerberoast",
            status: "in_progress",
            priority: "high",
            dependencies: ["i1"],
          },
        ],
        updatedAtMs: 1234,
      });
      expect(parsed.success).toBe(true);
    });

    it("accepts a goal-less plan", () => {
      const parsed = planSchema.safeParse({
        type: "plan",
        items: [{ id: "i1", title: "x", status: "pending" }],
      });
      expect(parsed.success).toBe(true);
    });

    it("accepts a null goal explicitly", () => {
      const parsed = planSchema.safeParse({ type: "plan", goal: null, items: [] });
      expect(parsed.success).toBe(true);
    });
  });

  describe("guidance-lifecycle", () => {
    it("accepts each of the six lifecycle states", () => {
      const states = [
        "queued",
        "delivered_to_runtime",
        "applied_to_model_step",
        "expired",
        "superseded",
        "undelivered",
      ] as const;
      for (const state of states) {
        const parsed = guidanceLifecycleSchema.safeParse({
          type: "guidance-lifecycle",
          state,
          guidanceId: "g_1",
          runId: "run_1",
        });
        expect(parsed.success, `state=${state}`).toBe(true);
        if (parsed.success) {
          expect(parsed.data.state).toBe(state);
        }
      }
    });

    it("accepts applied + superseded aux ids", () => {
      const parsed = guidanceLifecycleSchema.safeParse({
        type: "guidance-lifecycle",
        state: "applied_to_model_step",
        guidanceId: "g_2",
        appliedMessageId: "msg_1",
        deliveredAtStep: 3,
        actorId: "op_1",
      });
      expect(parsed.success).toBe(true);
    });

    it("rejects an out-of-vocabulary lifecycle state", () => {
      const parsed = guidanceLifecycleSchema.safeParse({
        type: "guidance-lifecycle",
        state: "flying",
        guidanceId: "g_3",
      });
      expect(parsed.success).toBe(false);
    });
  });

  describe("discriminated union dispatch", () => {
    it("routes each known discriminator to its branch", () => {
      const samples: Array<{ type: string } & Record<string, unknown>> = [
        { type: "tool-invocation", toolCallId: "c", toolName: "t", state: "call" },
        { type: "command-output", toolName: "t", text: "x" },
        { type: "operational-trace", stage: "s", text: "x" },
        { type: "hitl-request", requestId: "r" },
        { type: "artifact", artifactId: "a" },
        { type: "reasoning", text: "x" },
        { type: "plan", items: [] },
        {
          type: "guidance-lifecycle",
          state: "queued",
          guidanceId: "g",
        },
      ];
      for (const sample of samples) {
        const parsed = muninUiV1PartSchema.safeParse(sample);
        expect(parsed.success, `dispatch for ${sample.type}`).toBe(true);
      }
    });
  });
});
