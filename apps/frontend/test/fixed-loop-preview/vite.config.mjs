import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import tailwindcss from 'tailwindcss';

const root = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(root, '../..');
export default defineConfig({
  root,
  resolve: { alias: { '@/components': resolve(root, 'common.ts'), '@': resolve(frontend, 'src') } },
  css: { postcss: { plugins: [tailwindcss({
    config: resolve(frontend, 'tailwind.config.js'),
    content: [resolve(root, '*.tsx'), resolve(frontend, 'src/pages/GroupChat/components/*.tsx'),
      resolve(frontend, 'src/components/{Button,Empty,Segmented}/**/*.tsx'), resolve(frontend, 'src/styles/**/*.ts')],
  })] } },
  server: { host: '127.0.0.1', port: 4181, strictPort: true, fs: { allow: [resolve(frontend, '../..')] } },
  build: { outDir: resolve(frontend, 'node_modules/.cache/fixed-loop-preview'), emptyOutDir: true },
});
