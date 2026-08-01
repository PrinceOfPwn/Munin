import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // This project is intentionally nested under the Python workspace. Pin the
  // trace root to the Next app so an unrelated lockfile above it cannot widen
  // production trace discovery or make builds depend on user-machine paths.
  outputFileTracingRoot: fileURLToPath(new URL(".", import.meta.url)),
  async rewrites() {
    const backend = process.env.MUNIN_BACKEND_URL ?? "http://localhost:8000";
    return [
      { source: "/api/backend/:path*", destination: `${backend}/api/:path*` },
    ];
  },
};

export default nextConfig;
