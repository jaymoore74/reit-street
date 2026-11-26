import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // self-contained server bundle for the container image (see Dockerfile)
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // native and Node-only packages stay out of the bundle
  serverExternalPackages: ["mysql2", "exceljs"],
  typedRoutes: false,
  async headers() {
    // CSP with a per-request nonce is set in src/proxy.ts; these apply to every response
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
