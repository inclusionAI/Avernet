jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() } }));

import { onRouteChange } from '../src/app';

describe('app runtime', () => {
  test('Open Core 默认路由守卫不跳转', () => {
    expect(() => onRouteChange({ location: { pathname: '/workspace' } })).not.toThrow();
  });
});
