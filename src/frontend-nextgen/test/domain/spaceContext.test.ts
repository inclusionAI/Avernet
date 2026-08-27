import type { Space } from '@/domain/admin/models';
import { filterJoinedSpaces, pickDefaultSpace, sortSpacesByDisplayOrder } from '@/domain/spaceContext';
import { describe, expect, it } from '@jest/globals';

const personal: Space = {
  spaceId: 10000,
  spaceCode: 'space_personal',
  spaceName: '个人空间',
  spaceType: 'PERSONAL',
  currentUserRole: 'ADMIN',
  isCreator: true,
  joinStatus: 'JOINED',
  memberCount: 1,
  ownerCount: 1,
  botCount: 0,
  gmtModified: '2026-08-01T10:00:00+08:00',
};

const teamJoined: Space = {
  spaceId: 10001,
  spaceCode: 'space_a8f93c21',
  spaceName: '风控团队',
  spaceType: 'TEAM',
  currentUserRole: 'ADMIN',
  isCreator: true,
  joinStatus: 'JOINED',
  memberCount: 5,
  ownerCount: 1,
  botCount: 8,
  gmtModified: '2026-08-12T11:58:00+08:00',
};

const teamAvailable: Space = {
  spaceId: 10002,
  spaceCode: 'space_aml',
  spaceName: '反洗钱团队',
  spaceType: 'TEAM',
  currentUserRole: undefined,
  joinStatus: 'NOT_JOINED',
  memberCount: 8,
  ownerCount: 1,
  botCount: 12,
  gmtModified: '2026-08-10T09:00:00+08:00',
};

const teamApplied: Space = {
  spaceId: 10003,
  spaceCode: 'space_applied',
  spaceName: '安全团队',
  spaceType: 'TEAM',
  currentUserRole: undefined,
  joinStatus: 'APPLYING',
  memberCount: 3,
  ownerCount: 1,
  botCount: 2,
  gmtModified: '2026-08-11T09:00:00+08:00',
};

describe('filterJoinedSpaces', () => {
  it('排除未加入(NOT_JOINED)与申请中(APPLYING)的团队空间，保留个人+已加入团队', () => {
    const out = filterJoinedSpaces([personal, teamJoined, teamAvailable, teamApplied]);
    expect(out.map((s) => s.spaceId)).toEqual([10000, 10001]);
  });

  it('全部已加入时原样返回', () => {
    const out = filterJoinedSpaces([personal, teamJoined]);
    expect(out).toHaveLength(2);
  });

  it('空数组返回空数组', () => {
    expect(filterJoinedSpaces([])).toEqual([]);
  });

  it('个人空间(JOINED)始终保留', () => {
    expect(filterJoinedSpaces([personal])).toEqual([personal]);
  });

  it('仅 joinStatus 缺失但 currentUserRole 是 ADMIN/MEMBER 时也视为已加入（双判兼容）', () => {
    const onlyRole: Space = { ...teamJoined, joinStatus: undefined };
    expect(filterJoinedSpaces([onlyRole])).toHaveLength(1);
  });
});

describe('pickDefaultSpace', () => {
  it('有个人空间时取第一个 PERSONAL', () => {
    expect(pickDefaultSpace([personal, teamJoined])?.spaceId).toBe(10000);
  });

  it('无个人空间时回落第一个已加入工作空间（团队）', () => {
    expect(pickDefaultSpace([teamJoined])?.spaceId).toBe(10001);
  });

  it('多个个人空间时取第一个', () => {
    const p2: Space = { ...personal, spaceId: 10010 };
    expect(pickDefaultSpace([personal, p2])?.spaceId).toBe(10000);
  });

  it('无空间（空数组）返回 undefined（不选中）', () => {
    expect(pickDefaultSpace([])).toBeUndefined();
  });
});

describe('sortSpacesByDisplayOrder', () => {
  const mkSpace = (id: number, type: 'PERSONAL' | 'TEAM', gmtCreate?: string, joined = true): Space =>
    ({
      spaceId: id,
      spaceCode: `s${id}`,
      spaceName: `空间${id}`,
      spaceType: type,
      joinStatus: joined ? 'JOINED' : 'NOT_JOINED',
      currentUserRole: joined ? 'ADMIN' : undefined,
      memberCount: 1,
      ownerCount: 1,
      botCount: 0,
      gmtCreate,
      gmtModified: gmtCreate ?? '',
    } as Space);

  it('三级排序：个人空间 > 有权限团队 > 未加入团队（按时间倒序）', () => {
    const r = sortSpacesByDisplayOrder([
      mkSpace(9, 'TEAM', '2026-08-01', false),
      mkSpace(2, 'TEAM', '2026-08-05', true),
      mkSpace(0, 'PERSONAL', '2026-08-01'),
      mkSpace(1, 'TEAM', '2026-08-10', true),
      mkSpace(8, 'TEAM', '2026-08-09', false),
    ]);
    expect(r.map((s) => s.spaceId)).toEqual([0, 1, 2, 8, 9]);
  });

  it('有权限团队间按创建时间倒序', () => {
    const r = sortSpacesByDisplayOrder([
      mkSpace(3, 'TEAM', '2026-08-01', true),
      mkSpace(1, 'TEAM', '2026-08-10', true),
      mkSpace(2, 'TEAM', '2026-08-05', true),
    ]);
    expect(r.map((s) => s.spaceId)).toEqual([1, 2, 3]);
  });

  it('未加入团队间按创建时间倒序', () => {
    const r = sortSpacesByDisplayOrder([
      mkSpace(9, 'TEAM', '2026-08-01', false),
      mkSpace(8, 'TEAM', '2026-08-10', false),
    ]);
    expect(r.map((s) => s.spaceId)).toEqual([8, 9]);
  });

  it('缺失 gmtCreate 的团队空间排末尾', () => {
    const r = sortSpacesByDisplayOrder([mkSpace(2, 'TEAM', undefined, true), mkSpace(1, 'TEAM', '2026-08-10', true)]);
    expect(r.map((s) => s.spaceId)).toEqual([1, 2]);
  });

  it('个人空间保持原序（不按时间排）', () => {
    const r = sortSpacesByDisplayOrder([mkSpace(1, 'PERSONAL', '2026-08-10'), mkSpace(0, 'PERSONAL', '2026-08-01')]);
    expect(r.map((s) => s.spaceId)).toEqual([1, 0]);
  });

  it('空列表/单元素稳定', () => {
    expect(sortSpacesByDisplayOrder([])).toEqual([]);
    const single = [mkSpace(1, 'TEAM', '2026-08-01', true)];
    expect(sortSpacesByDisplayOrder(single)).toEqual(single);
  });
});
