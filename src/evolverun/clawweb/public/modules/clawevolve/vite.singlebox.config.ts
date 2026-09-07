import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  root: resolve(import.meta.dirname, "web"),
  esbuild: { jsx: "automatic" },
  build: {
    outDir: resolve(import.meta.dirname, "dist/singlebox"),
    emptyOutDir: false,
    rollupOptions: {
      input: resolve(import.meta.dirname, "web/index.html"),
    },
  },
});
