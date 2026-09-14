import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  root: resolve(import.meta.dirname, "web"),
  plugins: [tailwindcss()],
  resolve: { dedupe: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"] },
  esbuild: { jsx: "automatic" },
  build: {
    outDir: resolve(import.meta.dirname, "dist/singlebox"),
    emptyOutDir: false,
    rollupOptions: {
      input: resolve(import.meta.dirname, "web/index.html"),
    },
  },
});
