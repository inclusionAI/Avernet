// test/services/backendApi/apiBaseUrl.test.ts
//
// URL 变体接缝契约:Open Core 默认恒等(相对路径原样);内部 Overlay 可注入改写规则并可复位。
// 变体注入后,两通道(httpClient executeBackendRequest / umi request 拦截器)共用同一规则点。

import { resolveApiUrl, setApiUrlResolver } from '@/services/backendApi/apiBaseUrl';

describe('apiBaseUrl resolver seam (Open Core 缺省恒等)', () => {
  afterEach(() => {
    setApiUrlResolver(null);
  });

  it('默认恒等:不注册 resolver 时原样返回相对路径与 query', () => {
    const url = '/openapi/v1/bots/spaces/1326/skills/1130579/draft/files/SKILL.md?user_id=231895';
    expect(resolveApiUrl(url)).toBe(url);
  });

  it('注入 resolver 后经其改写(保留 query)', () => {
    setApiUrlResolver((url) => `https://gateway.example.com${url}`);
    expect(resolveApiUrl('/openapi/v1/x?user_id=1')).toBe(
      'https://gateway.example.com/openapi/v1/x?user_id=1',
    );
  });

  it('setApiUrlResolver(null) 恢复恒等', () => {
    setApiUrlResolver((url) => `https://gateway.example.com${url}`);
    expect(resolveApiUrl('/openapi/v1/x')).not.toBe('/openapi/v1/x');
    setApiUrlResolver(null);
    expect(resolveApiUrl('/openapi/v1/x')).toBe('/openapi/v1/x');
  });

  it('非字符串防御:空串原样返回', () => {
    expect(resolveApiUrl('')).toBe('');
  });
});
