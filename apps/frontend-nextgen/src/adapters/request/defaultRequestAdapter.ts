import type { RuntimeRequestAdapter } from './types';

// Open Core 默认请求适配器只做安全透传，内部代理和身份能力由 Internal Overlay 注入。
export const defaultRequestAdapter: RuntimeRequestAdapter = (config) => ({
  ...config,
  headers: {
    ...config.headers,
  },
});
