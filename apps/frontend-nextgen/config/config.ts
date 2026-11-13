import tailwindcss from '@tailwindcss/postcss';
import { defineConfig } from '@umijs/max';
import path from 'path';
import { a2aClientBuildDefault } from './a2aBuildDefault';
import { routes as openRoutes } from './routes';
import { routes as collabRoutes } from './routes.collab';

// 装配形态开关：open（默认，Open Core 全量）/ collab（协作群极简站）。
// 仅切换路由表（产物页面集）与 @/extensions 装配别名；业务组件内不出现形态分支。
const frontendForm = process.env.FRONTEND_FORM === 'collab' ? 'collab' : 'open';

const bcsEndpointPre = process.env.BCS_ENDPOINT_PRE || 'http://127.0.0.1:21000';
const bcsEndpointProd = process.env.BCS_ENDPOINT_PROD || 'http://127.0.0.1:21000';

export default defineConfig({
  routes: frontendForm === 'collab' ? collabRoutes : openRoutes,
  alias: {
    // 精确匹配键：非 `$` alias 键在 webpack/enhanced-resolve 里是前缀匹配——不带 `$` 时
    // `@/extensions/slots` 等子路径会被改写为 `<assembly>/slots` 而解析失败（jest 不受影响）。
    '@/extensions$': path.resolve(
      __dirname,
      frontendForm === 'collab' ? '../src/extensions/collab' : '../src/extensions/empty',
    ),
    // 公共 A2A 聊天客户端包（packages/a2a-chat-client，src 源直连——TS-source 包，
    // jsx-ts-tsx 规则无 exclude 照转译；不经 node_modules 故不入 MFSU 预构建）。
    '@avernet/a2a-chat-client':
      path.resolve(__dirname, '../../../packages/a2a-chat-client/src/index.ts'),
    '@avernet/a2a-chat-client/testing':
      path.resolve(__dirname, '../../../packages/a2a-chat-client/src/testing.ts'),
  },
  npmClient: 'npm',
  // 首屏骨架屏注入（见 config/plugins/boot-splash.ts）：HTML 空窗期给用户品牌级反馈，
  // React mount 时骨架自动退场；Open Core 与内部组装共用本插件。
  plugins: ['./config/plugins/boot-splash'],
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
    // 可选 Backend 增强开关（BCS 独立好友发现）：默认全栈开启；本地 BCS-only 排障经
    // TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED=false 编译关闭（值为**布尔**，与 QWENWORK 同法；
    // 部署态运行时 window.AVERNET_RUNTIME_CONFIG.PUBLIC_BOT_BACKEND_ENABLED 优先，见 defaultCapabilities）。
    TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED: process.env.TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED !== 'false',
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
