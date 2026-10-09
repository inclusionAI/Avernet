import { getPlatform, isWeb } from '../../src/utils/platform';

describe('platform', () => {
  test('node 测试环境默认按 web 安全降级', () => {
    expect(getPlatform()).toBe('web');
    expect(isWeb()).toBe(true);
  });
});
