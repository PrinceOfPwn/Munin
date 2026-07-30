/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const backend = process.env.MUNIN_BACKEND_URL ?? "http://localhost:8000";
    return [
      { source: "/api/backend/:path*", destination: `${backend}/api/:path*` },
    ];
  },
};

export default nextConfig;
