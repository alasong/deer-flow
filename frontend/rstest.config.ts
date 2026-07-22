import { resolve } from "path";

import { pluginReact } from "@rsbuild/plugin-react";
import { defineConfig } from "@rstest/core";

export default defineConfig({
  plugins: [pluginReact()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  output: {
    // Streamdown imports KaTeX CSS as a side effect. Bundle these packages so
    // Rsbuild processes that CSS import instead of Node trying to load it.
    bundleDependencies: ["streamdown", "katex"],
  },
  include: ["tests/unit/**/*.test.ts"],
  // Use worker threads instead of child-process forks for ~15% faster runs.
  pool: { type: "threads" },
  // Match available cores for maximum parallelism.
  maxConcurrency: 10,
});
