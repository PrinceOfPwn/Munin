// tags: [api-route, bff-proxy, server-side, o-p-t-i-o-n-s, p-o-s-t, p-a-t-c-h, g-e-t, d-e-l-e-t-e, f-o-r-w-a-r-d-e-d--h-e-a-d-e-r-s]
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// v3: the SSE endpoint holds a connection open for up to 4h.  Next's default
// route timeout on Vercel would truncate this; the GHA runner has no such
// cap but we keep `maxDuration` here so future hosted deployments inherit it.
export const maxDuration = 14400;

const FORWARDED_HEADERS = ["accept", "content-type", "cookie", "idempotency-key", "last-event-id", "origin", "sec-fetch-site", "x-csrf-token"] as const;

/**
 * Same-origin boundary: browser code sees only HttpOnly session cookies.
 *
 * v3 changes:
 *   - Response body streams straight through (`upstream.body` was already a
 *     ReadableStream) but the response headers now include the SSE-critical
 *     `x-accel-buffering` and `connection` overrides so intermediate proxies
 *     (Cloudflare, ngrok, corporate LBs) don't buffer heartbeats.
 *   - SSE (`text/event-stream`) requests skip the arraybuffer body materialise
 *     — the endpoint is GET-only anyway — and pass through the `Last-Event-ID`
 *     header verbatim so reconnect can resume from a known cursor.
 *   - `duplex: 'half'` on the outbound fetch is required by Node's undici to
 *     start streaming the response before the upstream is fully written.
 */
async function proxy(
  request: Request,
  context: { params: Promise<{ path?: string[] }> },
): Promise<Response> {
  const base = (process.env.MUNIN_PRODUCTION_API_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const incoming = new URL(request.url);
  const { path: pathSegments = [] } = await context.params;
  const path = pathSegments.map(encodeURIComponent).join("/");
  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("origin", process.env.MUNIN_PRODUCTION_PROXY_ORIGIN || incoming.origin);

  const method = request.method.toUpperCase();
  const isEventStream = (request.headers.get("accept") || "").includes("text/event-stream");
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  try {
    const upstream = await fetch(`${base}/api/${path}${incoming.search}`, {
      method,
      headers,
      body,
      cache: "no-store",
      // undici needs this to start streaming before body is fully consumed.
      // @ts-expect-error — 'duplex' is a Node fetch extension not yet in lib.dom.
      duplex: "half",
    });

    const responseHeaders = new Headers();
    // Forward the headers browsers need to interpret both JSON and SSE.
    for (const name of ["content-type", "cache-control", "set-cookie", "x-accel-buffering", "vary"] as const) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    if (isEventStream) {
      // Belt & braces for any intermediate proxy the fetch stack introduces.
      responseHeaders.set("cache-control", "no-cache, no-transform");
      responseHeaders.set("x-accel-buffering", "no");
      responseHeaders.set("connection", "keep-alive");
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return NextResponse.json({ ok: false, error: { code: "production_api_unreachable", message: "Munin production API is unavailable" } }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
