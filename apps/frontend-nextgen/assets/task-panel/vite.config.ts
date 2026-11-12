import { defineConfig } from 'vite';
import { readFileSync } from 'node:fs';
const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));
export default defineConfig({
  define: { __TASK_PANEL_VERSION__: JSON.stringify(version), 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    lib: { entry: 'src/index.ts', name: 'taskPanel', formats: ['umd'], fileName: () => 'index.umd.js' },
    rollupOptions: { external: ['react', 'react-dom'], output: {
      globals: { react: 'React', 'react-dom': 'ReactDOM' },
    } },
  },
  esbuild: { jsx: 'transform', jsxFactory: 'React.createElement', jsxFragment: 'React.Fragment' },
});
