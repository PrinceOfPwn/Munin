"use client";

import { useState } from "react";
import { Loader2, Play, Check, X } from "lucide-react";
import JsonViewer from "./JsonViewer";
import { useMuninStore } from "@/store/muninStore";
import { getMcpClient, extractToolResultContent } from "@/lib/mcp";
import { categorize } from "@/lib/categories";
import { formatDuration } from "@/lib/format";
import type { McpTool } from "@/types/mcp";

interface ToolRunFormProps {
  tool: McpTool;
  onDone?: () => void;
}

export default function ToolRunForm({ tool, onDone }: ToolRunFormProps) {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const refreshLive = useMuninStore((s) => s.refreshLive);

  const props = tool.inputSchema?.properties || {};
  const required = tool.inputSchema?.required || [];

  const [values, setValues] = useState<Record<string, any>>(() => {
    const init: Record<string, any> = {};
    for (const [k, schema] of Object.entries(props)) {
      if (schema.default !== undefined) init[k] = schema.default;
      else init[k] = "";
    }
    return init;
  });

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const cat = categorize(tool.name);

  const validate = (): { ok: boolean; errors: Record<string, string> } => {
    const errs: Record<string, string> = {};
    for (const req of required) {
      const v = values[req];
      // Required means "must have a non-empty value". Zero is a valid number value,
      // false is a valid boolean value — only reject empty string / null / undefined.
      if (v === "" || v === null || v === undefined) {
        errs[req] = "This field is required";
        continue;
      }
      const schema = props[req];
      if (schema?.type === "string" && typeof v === "string" && !v.trim()) {
        errs[req] = "This field is required";
      }
    }
    // Extra sanity for JSON-typed fields: don't submit malformed JSON.
    for (const [k, v] of Object.entries(values)) {
      const schema = props[k];
      if (!schema) continue;
      if ((schema.type === "object" || schema.type === "array") && typeof v === "string" && v.trim()) {
        try {
          JSON.parse(v);
        } catch {
          errs[k] = `Invalid JSON: expected ${schema.type}`;
        }
      }
    }
    return { ok: Object.keys(errs).length === 0, errors: errs };
  };

  const handleRun = async () => {
    // Client-side validation FIRST — never submit missing/malformed args.
    // Fixes issue #5: fetch_agent_messages requires recipient_agent; soul_read
    // requires path. Previously the form let the user click Run with empty
    // required fields and the server bounced back with a pydantic error message.
    const { ok: valid, errors } = validate();
    setFieldErrors(errors);
    if (!valid) {
      setIsError(true);
      const missing = Object.keys(errors).join(", ");
      setError(`Missing/invalid required fields: ${missing}`);
      return;
    }

    setRunning(true);
    setResult(null);
    setError(null);
    setIsError(false);
    setElapsed(null);

    // Coerce values before shipping to MCP. Never JSON-encode a string primitive
    // twice — issue #5 (soul_read) was caused by `{"name": "path": "identity.md"}`
    // arriving where a plain string was expected. `coerceValue` handles this by
    // only JSON.parse-ing for schema.type in ("array", "object"); strings pass
    // through verbatim.
    const args: Record<string, any> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v === "" || v == null) continue;
      const schema = props[k];
      const coerced = coerceValue(v, schema);
      if (coerced !== undefined) args[k] = coerced;
    }

    const start = performance.now();
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool(tool.name, args);
      const ms = Math.round(performance.now() - start);
      setElapsed(ms);
      const { text, json, isError: ie } = extractToolResultContent(r);
      setIsError(ie);
      if (ie) {
        setError(text || "Tool returned an error");
        setResult(json !== undefined ? json : text);
      } else {
        setResult(json !== undefined ? json : text);
      }
      // Refresh live state (some tools affect memory/wake/forge counts)
      refreshLive();
    } catch (e: any) {
      const ms = Math.round(performance.now() - start);
      setElapsed(ms);
      setIsError(true);
      setError(e?.message || String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="p-4 space-y-4">
      {/* Description */}
      {tool.description && (
        <div className="text-sm text-muted">{tool.description}</div>
      )}

      {/* Form */}
      <div className="space-y-3">
        {Object.entries(props).length === 0 ? (
          <div className="text-xs text-muted font-mono">
            This tool takes no arguments.
          </div>
        ) : (
          Object.entries(props).map(([k, schema]) => (
            <FieldInput
              key={k}
              name={k}
              schema={schema}
              required={required.includes(k)}
              value={values[k]}
              onChange={(v) => {
                setValues((s) => ({ ...s, [k]: v }));
                // Clear this field's error as soon as the user starts fixing it.
                if (fieldErrors[k]) {
                  setFieldErrors((f) => {
                    const { [k]: _dropped, ...rest } = f;
                    return rest;
                  });
                }
              }}
              error={fieldErrors[k]}
            />
          ))
        )}
      </div>

      <button
        onClick={handleRun}
        disabled={running}
        className="w-full flex items-center justify-center gap-1.5 text-sm font-mono uppercase tracking-wider py-2 rounded border transition-colors disabled:opacity-50"
        style={{
          borderColor: cat.color + "66",
          color: cat.color,
          backgroundColor: cat.color + "11",
        }}
      >
        {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
        {running ? "Running…" : "Run tool"}
      </button>

      {/* Result */}
      {result !== null && (
        <div
          className="rounded border p-3"
          style={{
            borderColor: isError ? "#f43f5e55" : cat.color + "44",
            backgroundColor: isError ? "#f43f5e08" : "transparent",
          }}
        >
          <div className="flex items-center justify-between mb-2">
            <div
              className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest font-mono"
              style={{ color: isError ? "#f43f5e" : "#10b981" }}
            >
              {isError ? <X size={12} /> : <Check size={12} />}
              {isError ? "Error" : "Result"}
            </div>
            {elapsed !== null && (
              <div className="text-[11px] text-muted font-mono">
                {formatDuration(elapsed)}
              </div>
            )}
          </div>

          {error && (
            <div className="text-rose text-xs font-mono break-all mb-2">
              {error}
            </div>
          )}

          <JsonViewer data={result} expanded maxExpandDepth={5} />
        </div>
      )}
    </div>
  );
}

function FieldInput({
  name,
  schema,
  required,
  value,
  onChange,
  error,
}: {
  name: string;
  schema: import("@/types/mcp").JsonSchemaProperty;
  required: boolean;
  value: any;
  onChange: (v: any) => void;
  error?: string;
}) {
  const type = schema.type || "string";
  const desc = schema.description || "";
  const label = `${name}${required ? " *" : ""}`;

  // Every input shares this styling; when an error is present we tint the border rose.
  const inputCls =
    "w-full bg-bg border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none " +
    (error ? "border-rose/60 focus:border-rose/80" : "border-border focus:border-accent/60");

  const labelBlock = (
    <div className="text-[10px] uppercase tracking-widest text-muted font-mono mb-1">
      {label}
      <span className="ml-1.5 text-accent/60">{type}</span>
      {required && <span className="ml-1 text-rose">required</span>}
    </div>
  );

  const errorBlock = error ? (
    <div className="text-[10px] text-rose font-mono mt-1">{error}</div>
  ) : null;

  const descBlock = desc && !error ? (
    <div className="text-[10px] text-muted mt-1">{desc}</div>
  ) : null;

  if (type === "boolean") {
    return (
      <label className="block">
        {labelBlock}
        <select
          value={value === "" ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? "" : e.target.value === "true")}
          className={inputCls}
        >
          <option value="">—</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
        {errorBlock}
        {descBlock}
      </label>
    );
  }

  if (schema.enum && schema.enum.length > 0) {
    return (
      <label className="block">
        {labelBlock}
        <select
          value={value || ""}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
        >
          <option value="">—</option>
          {schema.enum.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </select>
        {errorBlock}
        {descBlock}
      </label>
    );
  }

  const isLong =
    type === "array" ||
    type === "object" ||
    (typeof value === "string" && value.length > 60) ||
    /filter|query|payload|body|content|markdown|text/i.test(name);

  return (
    <label className="block">
      {labelBlock}
      {isLong ? (
        <textarea
          value={typeof value === "string" ? value : JSON.stringify(value, null, 2)}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          className={inputCls}
          placeholder={type === "object" ? "{}" : type === "array" ? "[]" : undefined}
        />
      ) : (
        <input
          type={type === "number" || type === "integer" ? "number" : "text"}
          value={value ?? ""}
          onChange={(e) =>
            onChange(
              type === "number" || type === "integer"
                ? e.target.value === ""
                  ? ""
                  : Number(e.target.value)
                : e.target.value
            )
          }
          className={inputCls}
        />
      )}
      {errorBlock}
      {descBlock}
    </label>
  );
}

function coerceValue(v: any, schema: import("@/types/mcp").JsonSchemaProperty): any {
  if (v === "" || v == null) return undefined;
  const type = schema.type || "string";
  if (type === "number" || type === "integer") {
    return type === "integer" ? parseInt(v, 10) : Number(v);
  }
  if (type === "boolean") {
    if (typeof v === "boolean") return v;
    return v === "true";
  }
  if (type === "array" || type === "object") {
    if (typeof v === "string") {
      try {
        return JSON.parse(v);
      } catch {
        return v;
      }
    }
    return v;
  }
  return v;
}
