import { defineConfig } from '@umijs/max';
import { join } from 'path';
import { routes } from './routes';

const isWebview = process.env.BUILD_TARGET === 'webview';

// umi/max 迁移 PoC（2026-06-12）：
// - 已剥离 bigfish/蚂蚁专属项：appType / deployMode:tern / tern.proxy / ctoken / monitor / tracert / asf-log preset
//   （正式迁移时由 config.internal.ts 经 UMI_ENV=internal 注入，tern 块见 git 历史 ac641f263:config/config.ts）
// - 开发代理沿用 config.local.ts（PRESET 机制不变）
export default defineConfig({
  favicons: [
    'https://mdn.alipayobjects.com/huamei_eqz4tv/afts/img/A*X-ZDRpkwqcMAAAAAQCAAAAgAeh2TAQ/original',
  ],
  outputPath: isWebview ? 'dist-webview' : process.env.OUTPUT_PATH || 'dist',
  publicPath: isWebview ? './' : process.env.PUBLIC_PATH || '/',
  // webview 环境使用 hash 路由，避免 vscode-webview:// 协议下 BrowserRouter 不工作
  ...(isWebview ? { history: { type: 'hash' } } : {}),
  qiankun: {
    master: {
      enable: true,
    },
  },
  initialState: {
    loading: '@/components/PageLoading/index',
  },
  request: {},
  model: {},
  routes,
  locale: { default: 'zh-CN', antd: true },
  // 开启 moment2dayjs 注入 dayjs 国际化，反之注入 moment 国际化
  mako: {},
  moment2dayjs: {},
  tailwindcss: {},
  antd: false,
  devtool: false,
  alias: {
    // capabilities @ext 注入入口：默认（开源构建）指向空实现。
    // 内部构建经 config.internal.ts 覆盖为 src/internal/index.ts（UMI_ENV=internal）。
    '@ext': join(__dirname, '../src/extensions/empty.ts'),
  },
});
