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

const RESPONSE_HEADERS = [
  "content-type",
  "mcp-protocol-version",
  "mcp-session-id",
] as const;

/**
 * Same-origin MCP proxy for the hosted GUI.
 *
 * A Next rewrite is convenient locally, but it can alter or drop the bearer
 * header before the request reaches FastMCP. This route copies the small MCP
 * header allow-list explicitly, keeping the browser token end-to-end intact.
 */
async function proxy(request: Request): Promise<Response> {
  const incomingUrl = new URL(request.url);
  const headers = new Headers();
  for (const name of MCP_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();
  const upstreamUrl = `http://127.0.0.1:8890${incomingUrl.pathname}${incomingUrl.search}`;

  try {
    const upstream = await fetch(upstreamUrl, { method, headers, body });
    const responseHeaders = new Headers();
    for (const name of RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
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
