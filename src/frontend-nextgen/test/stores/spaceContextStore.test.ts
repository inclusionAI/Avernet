import type { Space } from '@/domain/admin/models';
import { useSpaceContextStore } from '@/stores/spaceContextStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

const personal: Space = {
  spaceId: 10000,
  spaceCode: 'p',
  spaceName: '个人空间',
  spaceType: 'PERSONAL',
  joinStatus: 'JOINED',
  memberCount: 1,
  ownerCount: 1,
  botCount: 0,
  gmtModified: '2026-08-01T10:00:00+08:00',
};
const team: Space = {
  spaceId: 10001,
  spaceCode: 't',
  spaceName: '风控团队',
  spaceType: 'TEAM',
  joinStatus: 'JOINED',
  memberCount: 5,
  ownerCount: 1,
  botCount: 8,
  gmtModified: '2026-08-12T11:58:00+08:00',
};

describe('spaceContextStore', () => {
  beforeEach(() => useSpaceContextStore.getState().reset());

  it('setCurrentSpaceId 联动推算 currentSpace', () => {
    useSpaceContextStore.getState().setSpaces([personal, team]);
    useSpaceContextStore.getState().setCurrentSpaceId(10001);
    const s = useSpaceContextStore.getState();
    expect(s.currentSpaceId).toBe(10001);
    expect(s.currentSpace?.spaceName).toBe('风控团队');
  });

  it('setCurrentSpaceId 在列表中找不到时 currentSpace 为 undefined', () => {
    useSpaceContextStore.getState().setSpaces([personal]);
    useSpaceContextStore.getState().setCurrentSpaceId(99999);
    const s = useSpaceContextStore.getState();
    expect(s.currentSpaceId).toBe(99999);
    expect(s.currentSpace).toBeUndefined();
  });

  it('setSpaces 当前 id 不在新列表中时回落 undefined', () => {
    useSpaceContextStore.getState().setSpaces([personal, team]);
    useSpaceContextStore.getState().setCurrentSpaceId(10001);
    useSpaceContextStore.getState().setSpaces([personal]); // team 被移除
    const s = useSpaceContextStore.getState();
    expect(s.currentSpaceId).toBeUndefined();
    expect(s.currentSpace).toBeUndefined();
  });

  it('setSpaces 当前 id 仍在列表中时保留', () => {
    useSpaceContextStore.getState().setSpaces([personal, team]);
    useSpaceContextStore.getState().setCurrentSpaceId(10001);
    useSpaceContextStore.getState().setSpaces([team, personal]);
    expect(useSpaceContextStore.getState().currentSpaceId).toBe(10001);
  });

  it('setSpaces ID 不变时复用旧 currentSpace 引用，避免下游级联重渲染', () => {
    const teamClone: Space = { ...team, spaceName: '风控团队' }; // 值相同但不同引用
    useSpaceContextStore.getState().setSpaces([personal, team]);
    useSpaceContextStore.getState().setCurrentSpaceId(10001);
    const refBefore = useSpaceContextStore.getState().currentSpace;
    expect(refBefore).toBe(team);
    // 重拉列表（新数组、新对象引用但 ID/值相同）→ currentSpace 引用不应变
    useSpaceContextStore.getState().setSpaces([personal, teamClone]);
    const refAfter = useSpaceContextStore.getState().currentSpace;
    expect(refAfter).toBe(refBefore); // 引用稳定
    expect(refAfter?.spaceName).toBe('风控团队');
  });

  it('setInitialized / reset 清零', () => {
    useSpaceContextStore.getState().setInitialized(true);
    expect(useSpaceContextStore.getState().initialized).toBe(true);
    useSpaceContextStore.getState().reset();
    expect(useSpaceContextStore.getState().initialized).toBe(false);
  });

  it('reset 清空全部状态', () => {
    useSpaceContextStore.getState().setSpaces([personal]);
    useSpaceContextStore.getState().setCurrentSpaceId(10000);
    useSpaceContextStore.getState().setLoading(true);
    useSpaceContextStore.getState().reset();
    const s = useSpaceContextStore.getState();
    expect(s.spaces).toEqual([]);
    expect(s.currentSpaceId).toBeUndefined();
    expect(s.currentSpace).toBeUndefined();
    expect(s.loading).toBe(false);
    expect(s.initialized).toBe(false);
  });

  it('setLoading / setError', () => {
    useSpaceContextStore.getState().setLoading(true);
    expect(useSpaceContextStore.getState().loading).toBe(true);
    useSpaceContextStore.getState().setError('加载失败');
    expect(useSpaceContextStore.getState().error).toBe('加载失败');
    useSpaceContextStore.getState().setError(undefined);
    expect(useSpaceContextStore.getState().error).toBeUndefined();
  });
});
