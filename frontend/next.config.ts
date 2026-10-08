import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Produção em Docker: um servidor Node mínimo (.next/standalone/server.js), só com
  // os arquivos de node_modules que o build realmente usa.
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
