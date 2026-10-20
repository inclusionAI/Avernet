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
  // /favicon.png 为新版品牌 mark（墨黑笔画 + 蓝绿节点，透明底）位图直出，浅/深标签栏均可读。
  favicons: ['/favicon.png'],
  title: 'Avernet',
  define: {
    QWENWORK_PROVIDER_SLUG: process.env.QWENWORK_PROVIDER_SLUG || '',
    BCS_ENDPOINT_PRE: bcsEndpointPre,
    BCS_ENDPOINT_PROD: bcsEndpointProd,
    // A2A 客户端构建期默认开关（spec stage2 §1 唯一读取口=adapters/a2a/switch.ts）。
    // 镜像构建参数 A2A_CLIENT_DEFAULT=true 时验证/灰度镜像整包默认开；
    // 主线构建不注入 → 语义为字符串 'false'，保持关闭。运行期 localStorage
    // '0' 依然优先级最高（现场回退通道不因构建期默认而失效）。
    __A2A_CLIENT_DEFAULT__: process.env.A2A_CLIENT_DEFAULT === 'true' ? "'true'" : "'false'",
  },
});
