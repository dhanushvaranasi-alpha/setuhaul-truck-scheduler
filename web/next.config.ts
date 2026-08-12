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
};

export default nextConfig;
