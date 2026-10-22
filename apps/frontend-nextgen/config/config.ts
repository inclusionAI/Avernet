import tailwindcss from '@tailwindcss/postcss';
import { defineConfig } from '@umijs/max';
import path from 'path';
import { a2aClientBuildDefault } from './a2aBuildDefault';
import { routes } from './routes';

const bcsEndpointPre = process.env.BCS_ENDPOINT_PRE || 'http://127.0.0.1:21000';
const bcsEndpointProd = process.env.BCS_ENDPOINT_PROD || 'http://127.0.0.1:21000';

export default defineConfig({
  routes,
  alias: {
    '@/extensions': path.resolve(__dirname, '../src/extensions/empty'),
    // 公共 A2A 聊天客户端包（packages/a2a-chat-client，src 源直连——TS-source 包，
    // jsx-ts-tsx 规则无 exclude 照转译；不经 node_modules 故不入 MFSU 预构建）。
    '@avernet/a2a-chat-client':
      path.resolve(__dirname, '../../../packages/a2a-chat-client/src/index.ts'),
    '@avernet/a2a-chat-client/testing':
      path.resolve(__dirname, '../../../packages/a2a-chat-client/src/testing.ts'),
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
    // A2A 客户端构建期默认开关（spec stage2 §1 唯一读取口=adapters/a2a/
    // switch.ts）。镜像构建参数 A2A_CLIENT_DEFAULT=true 时验证/灰度镜像
    // 整包默认开；主线构建不注入保持关闭。值为**布尔**（review P2 修复：
    // 旧 "'true'" 带引号字面量经 umi define 转换后是四字符字符串、恒 !==
    // 'true'，灰度镜像实际全在跑 v3）；计算单源 config/a2aBuildDefault.ts
    // （可直测，umi 配置链 jest 驱动不了）。运行期 localStorage '0' 依然
    // 优先级最高（现场回退通道不因构建期默认而失效）。
    __A2A_CLIENT_DEFAULT__: a2aClientBuildDefault(),
  },
});
