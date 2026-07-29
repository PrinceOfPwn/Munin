import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FORWARDED_HEADERS = ["accept", "content-type", "cookie", "idempotency-key", "last-event-id", "origin", "sec-fetch-site", "x-csrf-token"] as const;

/** Same-origin boundary: browser code sees only HttpOnly session cookies. */
async function proxy(request: Request, context: { params: { path?: string[] } }): Promise<Response> {
  const base = (process.env.MUNIN_PRODUCTION_API_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const incoming = new URL(request.url);
  const path = (context.params.path || []).map(encodeURIComponent).join("/");
  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();
  try {
    const upstream = await fetch(`${base}/api/${path}${incoming.search}`, { method, headers, body, cache: "no-store" });
    const responseHeaders = new Headers();
    for (const name of ["content-type", "cache-control", "set-cookie"] as const) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
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
