import { describe, it, expect } from "vitest";
import { partRenderers, getPartRenderer } from "@/lib/partsRenderers";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("partRenderers map", () => {
  const EXPECTED_KEYS = [
    // Standard AI SDK part types
    "reasoning",
    "tool-invocation",
    // Custom part ids
    "subagent-presence",
    "hitl-request",
    "artifact",
    "heartbeat",
    "note",
    "guidance",
    // Synthetic grouping
    "parallel-tool",
  ];

  it("contains entries for all expected part types / ids", () => {
    for (const key of EXPECTED_KEYS) {
      expect(partRenderers).toHaveProperty(key);
      expect(typeof partRenderers[key]).toBe("function");
    }
  });

  it("does not contain undefined entries", () => {
    for (const [key, value] of Object.entries(partRenderers)) {
      expect(value, `renderer for "${key}" should not be undefined`).toBeDefined();
    }
  });
});

describe("getPartRenderer", () => {
  it("resolves a standard part by type", () => {
    const renderer = getPartRenderer({ type: "reasoning", text: "..." });
    expect(renderer).toBeDefined();
    expect(typeof renderer).toBe("function");
  });

  it("resolves a custom part by id, not type", () => {
    const renderer = getPartRenderer({ type: "custom", id: "hitl-request" });
    expect(renderer).toBeDefined();
    expect(typeof renderer).toBe("function");
  });

  it("returns undefined for a part type with no registered renderer", () => {
    const renderer = getPartRenderer({ type: "text" });
    expect(renderer).toBeUndefined();
  });

  it("returns undefined for a custom part with an unregistered id", () => {
    const renderer = getPartRenderer({ type: "custom", id: "nonexistent-id" });
    expect(renderer).toBeUndefined();
  });

  it("returns undefined for null input", () => {
    const renderer = getPartRenderer(null);
    expect(renderer).toBeUndefined();
  });

  it("returns undefined for non-object input", () => {
    const renderer = getPartRenderer("string");
    expect(renderer).toBeUndefined();
  });

  it("resolves tool-invocation parts", () => {
    const renderer = getPartRenderer({
      type: "tool-invocation",
      toolCallId: "tc-1",
      toolName: "web_search",
      state: "call",
    });
    expect(renderer).toBeDefined();
  });

  it("resolves subagent-presence custom parts", () => {
    const renderer = getPartRenderer({
      type: "custom",
      id: "subagent-presence",
      subagentId: "sa-1",
      name: "Recon",
      state: "running",
    });
    expect(renderer).toBeDefined();
  });

  it("resolves artifact custom parts", () => {
    const renderer = getPartRenderer({
      type: "custom",
      id: "artifact",
      artifactId: "art-1",
      mimeType: "application/pdf",
      uri: "https://example.com/file.pdf",
    });
    expect(renderer).toBeDefined();
  });
});
