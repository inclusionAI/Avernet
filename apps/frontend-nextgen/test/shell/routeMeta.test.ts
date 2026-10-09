import { getMergedRouteMetas, getRouteMeta, routeMetaList } from '@/shell/routeMeta';
import { describe, expect, it } from '@jest/globals';

describe('routeMeta 路由归属契约', () => {
  it('对话 / 协作群路由按约定落位（navKey + 标题），默认 capability 注入为空时合并结果=基线', () => {
    expect(getRouteMeta('/workspace/chat')?.navKey).toBe('conversation');
    expect(getRouteMeta('/workspace/chat')?.title).toBe('对话');
    expect(getRouteMeta('/workspace/collaboration')?.navKey).toBe('collaboration');
    expect(getRouteMeta('/workspace/collaboration')?.title).toBe('协作群');
    expect(getMergedRouteMetas()).toEqual(routeMetaList);
  });

  it('/workspace 不再占导航位（无 navKey）；子路由按最长前缀解析', () => {
    expect(getRouteMeta('/workspace')?.navKey).toBeUndefined();
    expect(getRouteMeta('/workspace/chat/session-1')?.navKey).toBe('conversation');
    expect(getRouteMeta('/workspace/collaboration/detail')?.navKey).toBe('collaboration');
  });
});
