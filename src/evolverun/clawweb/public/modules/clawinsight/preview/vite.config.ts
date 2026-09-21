import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';
import { createPreviewApp } from './server';
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [react(), tailwindcss(), {
    name: 'monitoring-local-api',
    async configureServer(server) {
      if (server.config.server.host !== '127.0.0.1') throw new Error('Monitoring preview must bind to 127.0.0.1');
      const preview = await createPreviewApp();
      server.middlewares.use(preview.app);
      server.httpServer?.once('close', () => { void preview.close(); });
    },
  }],
  server: { host: '127.0.0.1', port: 3101, strictPort: true },
});
