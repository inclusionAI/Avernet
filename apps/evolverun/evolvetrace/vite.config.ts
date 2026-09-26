import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/') || id.includes('node_modules/react-router-dom/')) {
            return 'vendor-react'
          }
          if (id.includes('node_modules/@tanstack/react-query/')) {
            return 'vendor-query'
          }
          if (id.includes('node_modules/@xyflow/react/')) {
            return 'vendor-xyflow'
          }
          if (id.includes('node_modules/react-markdown/') || id.includes('node_modules/remark-gfm/') || id.includes('node_modules/rehype-raw/')) {
            return 'vendor-markdown'
          }
          if (id.includes('node_modules/highlight.js/')) {
            return 'vendor-highlight'
          }
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5176,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:3002',
        changeOrigin: true,
      },
    },
  },
})
