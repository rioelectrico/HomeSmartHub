import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const backendOrigin = (process.env.BACKEND_ORIGIN ?? "http://localhost:8000").replace(/\/$/, "");
    return [{ source: "/api/:path*", destination: `${backendOrigin}/api/:path*` }];
  },
};

export default nextConfig;
