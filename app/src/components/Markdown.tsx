"use client";

import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Renderer for assistant text parts.  `react-markdown` + GFM + highlight.js
// are already installed but were unused; this is the single place that turns
// raw markdown into styled DOM.  Styling consumes the project tokens directly
// (no @tailwindcss/typography dependency).
// ---------------------------------------------------------------------------

const markdownComponents: Components = {
  a({ href, children, ...props }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-accent underline decoration-accent/40 underline-offset-2 hover:text-accent-hover"
        {...props}
      >
        {children}
      </a>
    );
  },
  p({ children }) {
    return <p className="my-1 whitespace-pre-wrap last:mb-0">{children}</p>;
  },
  ul({ children }) {
    return <ul className="my-1 list-disc space-y-0.5 pl-5">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="my-1 list-decimal space-y-0.5 pl-5">{children}</ol>;
  },
  li({ children }) {
    return <li className="leading-relaxed">{children}</li>;
  },
  h1({ children }) {
    return <h1 className="mb-1 mt-2 text-base font-semibold text-body">{children}</h1>;
  },
  h2({ children }) {
    return <h2 className="mb-1 mt-2 text-[0.95rem] font-semibold text-body">{children}</h2>;
  },
  h3({ children }) {
    return <h3 className="mb-0.5 mt-2 text-sm font-semibold text-body">{children}</h3>;
  },
  h4({ children }) {
    return <h4 className="mb-0.5 mt-1.5 text-sm font-medium text-body">{children}</h4>;
  },
  strong({ children }) {
    return <strong className="font-semibold text-body">{children}</strong>;
  },
  em({ children }) {
    return <em className="text-body">{children}</em>;
  },
  hr() {
    return <hr className="my-2 border-border" />;
  },
  blockquote({ children }) {
    return (
      <blockquote className="my-1 border-l-2 border-accent/50 pl-3 text-secondary">
        {children}
      </blockquote>
    );
  },
  // rehype-highlight marks fenced blocks with `hljs` + language class; inline
  // code carries no class, so the className decides block vs inline styling.
  code({ className, children, ...props }) {
    const isBlock = Boolean(className && className.includes("hljs"));
    if (isBlock) {
      return (
        <code className={cn("font-mono text-[0.8em] leading-relaxed", className)} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code
        className="rounded border border-border bg-active px-1 py-px font-mono text-[0.85em] text-accent-hover"
        {...props}
      >
        {children}
      </code>
    );
  },
  pre({ children }) {
    return (
      <pre className="my-2 overflow-x-auto rounded-md border border-border bg-active p-3 font-mono text-xs leading-relaxed">
        {children}
      </pre>
    );
  },
  table({ children }) {
    return (
      <div className="my-2 overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse text-left text-sm">{children}</table>
      </div>
    );
  },
  thead({ children }) {
    return <thead className="border-b border-border bg-raised">{children}</thead>;
  },
  th({ children }) {
    return <th className="px-3 py-1.5 font-medium text-secondary">{children}</th>;
  },
  td({ children }) {
    return <td className="border-t border-border px-3 py-1.5 text-body">{children}</td>;
  },
  img({ src, alt }) {
    // eslint-disable-next-line @next/next/no-img-element -- remote evidence screenshots
    return <img src={src} alt={alt ?? ""} className="my-2 max-w-full rounded-md border border-border" />;
  },
};

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn("text-sm leading-relaxed text-body", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={markdownComponents}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
