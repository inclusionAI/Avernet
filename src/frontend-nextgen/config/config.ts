import tailwindcss from '@tailwindcss/postcss';
import { defineConfig } from '@umijs/max';
import path from 'path';
import { routes } from './routes';

export default defineConfig({
  routes,
  alias: {
    '@/extensions': path.resolve(__dirname, '../src/extensions/empty'),
  },
  npmClient: 'npm',
  extraPostCSSPlugins: [tailwindcss()],
  antd: false,
  request: {},
  codeSplitting: { jsStrategy: 'granularChunks' },
  esbuildMinifyIIFE: true,
  favicons: ['/favicon.svg'],
  title: 'Avernet',
});
