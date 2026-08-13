import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Dev-only: on Vercel, api/*.py deploys under the same origin as this app
  // (see ../vercel.json's cron paths and the wider deployment-topology
  // question noted in the Step 15/16 commit — still undecided). Locally
  // there's nothing serving /api/* unless something else does, so proxy to
  // the local dev dispatcher (scratchpad/local_api_server.py) instead.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
  },
  experimental: {
    // Next's dev rewrite proxy kills the upstream connection at 30s by
    // default (server/lib/router-utils/proxy-request.js) and returns its
    // own 500 to the browser. /api/chat can legitimately run several
    // sequential tool-calling LLM round trips past that — bump it well
    // above any realistic single chat turn rather than the default.
    proxyTimeout: 120_000,
  },
};

export default nextConfig;
