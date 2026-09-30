import tailwindcss from '@tailwindcss/postcss';
import { defineConfig } from '@umijs/max';
import path from 'path';
import { routes } from './routes';

const bcsEndpointPre = process.env.BCS_ENDPOINT_PRE || 'http://127.0.0.1:21000';
const bcsEndpointProd = process.env.BCS_ENDPOINT_PROD || 'http://127.0.0.1:21000';

export default defineConfig({
  routes,
  alias: {
    '@/extensions': path.resolve(__dirname, '../src/extensions/empty'),
  },
  npmClient: 'npm',
  headScripts: [{ src: '/runtime-config.js' }],
  extraPostCSSPlugins: [tailwindcss()],
  antd: false,
  request: {},
  codeSplitting: { jsStrategy: 'granularChunks' },
  esbuildMinifyIIFE: true,
  // Open（Avernet）形态的标签页标题/favicon；Internal 事实在核心 config/config.ts，两处同 PR 成对修改。
  // /avernet-favicon.svg 为方版 mark 衍生的简化矢量（16px 可读，见 public/avernet-favicon.svg）。
  favicons: ['/avernet-favicon.svg'],
  title: 'Avernet',
  define: {
    BCS_ENDPOINT_PRE: bcsEndpointPre,
    BCS_ENDPOINT_PROD: bcsEndpointProd,
  },
});
