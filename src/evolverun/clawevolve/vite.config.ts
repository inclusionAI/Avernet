import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  esbuild: {
    jsx: "automatic",
  },
  build: {
    outDir: "dist/web",
    emptyOutDir: false,
    lib: {
      entry: resolve(import.meta.dirname, "web/src/index.ts"),
      formats: ["es"],
      fileName: () => "clawevolve.js",
    },
    rollupOptions: {
      external: [
        "react",
        "react/jsx-runtime",
        "react-router-dom",
        "@tanstack/react-query",
        "react-markdown",
        "remark-gfm",
      ],
    },
  },
});
