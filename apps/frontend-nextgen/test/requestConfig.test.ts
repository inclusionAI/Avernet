import { defaultRequestAdapter } from '../src/adapters/request';
import { request } from '../src/requestConfig';

describe('requestConfig', () => {
  test('默认 request adapter 安全透传', () => {
    expect(defaultRequestAdapter({ url: '/openapi/v1/bots', headers: { foo: 'bar' } }, { platform: 'web' })).toEqual({
      url: '/openapi/v1/bots',
      headers: { foo: 'bar' },
    });
  });

  test('导出 Bigfish/Umi request 运行时配置', () => {
    expect(request.withCredentials).toBe(true);
    expect(request.requestInterceptors).toHaveLength(1);
    expect(request.responseInterceptors).toHaveLength(1);
    expect(request.errorConfig?.errorHandler).toBeInstanceOf(Function);
  });
});
