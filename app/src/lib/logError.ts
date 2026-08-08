// tags: [utility-library, error-logging, logError, PR-4C-fallback, frontend-error-contract]
// -----------------------------------------------------------------------------
// logError — minimal inline fallback for the PLAN-4.C structured logger.
//
// SDK contract: every try/catch in app/src/** that handles an error MUST
// surface it through a helper with the signature
//   logError({ context, error, meta, ts })
// Errors are never swallowed silently. A silent catch is an instant PR
// reject.
//
// This file is the canonical home until PLAN-4.C ships the full
// observable/replayable logger. It keeps the call shape stable so the
// eventual drop-in replacement only touches this module.
// -----------------------------------------------------------------------------

export interface LogErrorInput {
  /** Stable caller name, e.g. "cancel", "renderer_error", "schema_validation". */
  context: string;
  /** The caught value (Error or unknown). */
  error: unknown;
  /** Optional structured metadata (rendererKey, runId, dataPart, ...). */
  meta?: Record<string, unknown>;
  /** Optional ISO timestamp; defaults to now. Tests can pin it. */
  ts?: string;
}

/**
 * Emit a structured console.error with the operator error contract.
 *
 * Output shape (single console.error argument so DevTools groups it):
 *   { context, error, meta, ts }
 *
 * `error` is preserved as the caught value (so the browser keeps the stack
 * link); we also coerce a message string for the human reading the console.
 */
export function logError(input: LogErrorInput): void {
  const { context, error, meta, ts } = input;
  const payload = {
    context,
    error,
    message: error instanceof Error ? error.message : String(error ?? ""),
    stack: error instanceof Error ? error.stack : undefined,
    meta: meta ?? {},
    ts: ts ?? new Date().toISOString(),
  };
  // `console.error` with one structured object groups cleanly in DevTools and
  // keeps the contract executable for any future CI error-assertion harness.
  console.error(payload);
}
