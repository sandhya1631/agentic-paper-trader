import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Pin the file-tracing root to this app so Next does not infer an unrelated
  // parent directory as the workspace root when other lockfiles exist upstream.
  outputFileTracingRoot: path.join(__dirname),
  webpack: (config) => {
    // Windows dev workaround: this project's real path contains an apostrophe
    // (C:\Sam's\...), accessed here via a junction at C:\dev\SSW695 that has
    // none. Webpack's default `resolve.symlinks: true` resolves the junction
    // back to its real target, reintroducing the apostrophe — which breaks
    // Next's metadata-route-loader (favicon/icon/opengraph-image conventions
    // embed the resolved path into generated module source as a JS string,
    // and the unescaped apostrophe corrupts it; see next-metadata-route-
    // loader.js's static-asset-route error message). Disabling symlink
    // resolution keeps the junction path as-is and avoids the bug entirely.
    config.resolve.symlinks = false;
    return config;
  },
};

export default nextConfig;
