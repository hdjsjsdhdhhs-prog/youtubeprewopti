import type { NextConfig } from "next";

// FastAPI origin. The browser only talks to Next; /api/* is proxied so the session cookie stays
// same-origin (no CORS, simple CSRF) — ADR-0005.
// NOTE: rewrites are resolved at `next build` time — set YTL_API_ORIGIN when building for deployment
// (`next start` does not re-read it). `next dev` reads it on start.
const apiOrigin = (process.env.YTL_API_ORIGIN ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  // Self-contained .next/standalone/server.js for the Docker image (frontend/Dockerfile).
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
};

export default nextConfig;
