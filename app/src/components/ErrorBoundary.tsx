"use client";

import { Component, type ReactNode, type ErrorInfo } from "react";
import { log } from "@/lib/logger";
import Raven from "./Raven";

interface Props {
  children: ReactNode;
  name?: string;
}

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

/**
 * React Error Boundary — wraps panels so a crash in one section
 * doesn't take down the whole app. Logs the full component stack.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null };

  componentDidCatch(error: Error, info: ErrorInfo) {
    const L = log.ns(`boundary:${this.props.name ?? "app"}`);
    L.error(
      `React render error in <${this.props.name ?? "unknown"}>`,
      error,
      { componentStack: info.componentStack }
    );
    this.setState({ error, info });
  }

  reset = () => this.setState({ error: null, info: null });

  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex flex-col items-center justify-center h-full p-8 gap-4">
        <Raven size={48} className="text-rose/40" eyeColor="#f43f5e" />
        <div className="text-center space-y-1">
          <p className="text-rose font-mono text-sm font-bold">
            Render error in {this.props.name ?? "component"}
          </p>
          <p className="text-muted text-xs font-mono">{error.message}</p>
        </div>

        {/* Stack trace — visible so devs can read it without opening DevTools */}
        <details className="w-full max-w-2xl">
          <summary className="text-[11px] text-muted font-mono cursor-pointer hover:text-body">
            Stack trace (also in DevTools console)
          </summary>
          <pre className="mt-2 text-[10px] text-rose/70 font-mono bg-surface border border-rose/20 rounded p-3 overflow-auto max-h-48 whitespace-pre-wrap break-all">
            {error.stack}
            {info?.componentStack && (
              <>
                {"\n\nComponent stack:"}
                {info.componentStack}
              </>
            )}
          </pre>
        </details>

        <button
          onClick={this.reset}
          className="px-4 py-1.5 text-xs font-mono uppercase tracking-wider border border-rose/40 text-rose rounded hover:bg-rose/10"
        >
          Retry
        </button>
      </div>
    );
  }
}
