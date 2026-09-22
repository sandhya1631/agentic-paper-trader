import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Pin the file-tracing root to this app so Next does not infer an unrelated
  // parent directory as the workspace root when other lockfiles exist upstream.
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;
