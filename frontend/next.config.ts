import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  turbopack: {},
  // Pin the file-tracing root to this app so Next does not infer an unrelated
  // parent directory as the workspace root when other lockfiles exist upstream.
  outputFileTracingRoot: path.join(__dirname),
  webpack: (config) => {
    // Windows dev workaround: if this repo is checked out under a symlink or
    // junction, webpack's default `resolve.symlinks: true` resolves it back
    // to the real target path. If that real path contains a character like an
    // apostrophe, it breaks Next's metadata-route-loader (favicon/icon/
    // opengraph-image conventions embed the resolved path into generated
    // module source as a JS string, and an unescaped apostrophe corrupts it).
    // Disabling symlink resolution keeps whatever path was used to start the
    // dev server as-is, avoiding the bug regardless of the real path.
    config.resolve.symlinks = false;
    return config;
  },
};

export default nextConfig;
