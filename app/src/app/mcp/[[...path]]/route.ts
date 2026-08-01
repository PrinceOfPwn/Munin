import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MCP_HEADERS = [
  "accept",
  "authorization",
  "content-type",
  "last-event-id",
  "mcp-protocol-version",
  "mcp-session-id",
] as const;

/**
 * Headers that must not be forwarded from upstream to the client (hop-by-hop).
 * All other response headers — including `mcp-session-id`, `mcp-protocol-version`,
 * `content-type`, `cache-control`, and streaming hints — are forwarded as-is.
 */
const HOP_BY_HOP_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

/**
 * Same-origin MCP proxy for the hosted GUI.
 *
 * A Next rewrite is convenient locally, but it can alter or drop the bearer
 * header before the request reaches FastMCP. This route copies the small MCP
 * header allow-list explicitly, keeping the browser token end-to-end intact.
 * A temporary, operator-approved live session can opt into server-side auth;
 * the secret then stays on the Next server and never reaches browser code.
 */
async function proxy(request: Request): Promise<Response> {
  const incomingUrl = new URL(request.url);
  const headers = new Headers();
  for (const name of MCP_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const serverToken = process.env.MUNIN_GUI_SERVER_AUTH_PROXY === "1"
    ? process.env.MUNIN_MCP_AUTH_TOKEN?.trim()
    : "";
  if (!headers.has("authorization") && serverToken) {
    headers.set("authorization", `Bearer ${serverToken}`);
  }

  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();
  // Fase 3 (issue #9): the unified backend now hosts MCP under /mcp on the
  // same port as the HTTP API, so the proxy target follows MUNIN_PRODUCTION_API_URL
  // (default 127.0.0.1:8787) instead of the retired :8890 MCP-only process.
  const upstreamBase = (process.env.MUNIN_PRODUCTION_API_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");
  // FastMCP serves streamable HTTP at `/mcp/`. Next canonicalizes the browser
  // route to `/mcp`, so normalize just the upstream target instead of sending
  // a redirect that breaks MCP session initialization through the proxy.
  const upstreamPath = incomingUrl.pathname === "/mcp" ? "/mcp/" : incomingUrl.pathname;
  const upstreamUrl = `${upstreamBase}${upstreamPath}${incomingUrl.search}`;

  try {
    const upstream = await fetch(upstreamUrl, { method, headers, body });
    // Forward all upstream response headers except hop-by-hop ones. This
    // ensures MCP protocol headers (`mcp-session-id`, `mcp-protocol-version`),
    // content negotiation headers, and any streaming hints reach the client
    // intact — a fixed allowlist would silently drop new MCP headers.
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, name) => {
      if (!HOP_BY_HOP_RESPONSE_HEADERS.has(name.toLowerCase())) {
        responseHeaders.set(name, value);
      }
    });
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json({ error: "Munin MCP is unreachable" }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
