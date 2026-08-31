import { describe, expect, it } from '@jest/globals';
import { routes } from '../config/routes';

describe('应用默认入口', () => {
  it('根路径默认进入工作台对话协作', () => {
    expect(routes[0]).toEqual({ path: '/', redirect: '/workspace' });
  });
});
