import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Shared tests also exercise workflow's schema guard through its package import.
  resolve: { alias: { '@avernet/clawweb-shared': fileURLToPath(new URL('.', import.meta.url)) } },
});
