// @jest/globals 必须先于被测模块导入：jest.mock 工厂在执行时会引用这里的 jest 绑定，
// 若被测模块先加载会触发工厂求值，此时 jest 绑定尚未初始化（undefined.jest 报错）。
import type { Space } from '@/domain/admin/models';
import { initSpaceContext, refreshSpaceContext, switchSpaceContext } from '@/hooks/useSpaceContext';
import { useSpaceContextStore } from '@/stores/spaceContextStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

// node jest 无 DOM：提供最小 localStorage global shim。
class LS {
  private m = new Map<string, string>();
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  clear() {
    this.m.clear();
  }
}
const ls = new LS();
(globalThis as unknown as { localStorage: LS }).localStorage = ls;

// mock adminService.listSpaces / ensurePersonalSpace
jest.mock('@/services/admin', () => ({
  adminService: {
    listSpaces: jest.fn(),
    ensurePersonalSpace: jest.fn(),
  },
}));

import { adminService } from '@/services/admin';
const mockedListSpaces = adminService.listSpaces as unknown as jest.MockedFunction<typeof adminService.listSpaces>;
const mockedEnsurePersonal = adminService.ensurePersonalSpace as unknown as jest.MockedFunction<
  typeof adminService.ensurePersonalSpace
>;

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
const teamAvailable: Space = {
  spaceId: 10002,
  spaceCode: 'a',
  spaceName: '反洗钱团队',
  spaceType: 'TEAM',
  joinStatus: 'NOT_JOINED',
  memberCount: 8,
  ownerCount: 1,
  botCount: 12,
  gmtModified: '2026-08-10T09:00:00+08:00',
};

describe('useSpaceContextActions', () => {
  beforeEach(() => {
    ls.clear();
    useSpaceContextStore.getState().reset(); // 同时清 initialized，使每次 init 可重新执行
    mockedListSpaces.mockReset();
    mockedEnsurePersonal.mockReset();
    mockedEnsurePersonal.mockResolvedValue({ data: true });
  });

  it('init：listSpaces 带 scope=accessible；不再前端过滤，后端返回什么用什么；默认个人空间', async () => {
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal, team, teamAvailable], total: 3, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    const s = useSpaceContextStore.getState();
    expect(mockedListSpaces).toHaveBeenCalledWith({ page: 1, pageSize: 100, scope: 'accessible' });
    expect(s.spaces.map((x) => x.spaceId)).toEqual([10000, 10001, 10002]); // 不再前端过滤，NOT_JOINED 也保留
    expect(s.currentSpaceId).toBe(10000); // 默认个人空间
    expect(s.loading).toBe(false);
    expect(s.error).toBeUndefined();
    expect(ls.getItem('tc_space_context_current_id')).toBe('10000');
    expect(s.initialized).toBe(true);
  });

  it('init：localStorage 命中已加入子集时还原该 id', async () => {
    ls.setItem('tc_space_context_current_id', '10001');
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal, team], total: 2, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    expect(useSpaceContextStore.getState().currentSpaceId).toBe(10001);
  });

  it('init：localStorage 中的 id 不在已加入列表（已退出/已失效）时回落个人空间', async () => {
    ls.setItem('tc_space_context_current_id', '99999'); // 不在后端 scope=accessible 返回的列表中
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal, team], total: 2, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    expect(useSpaceContextStore.getState().currentSpaceId).toBe(10000);
    expect(ls.getItem('tc_space_context_current_id')).toBe('10000'); // 修正持久化
  });

  it('init：无个人空间 → ensure + 重拉仍无 → currentSpaceId undefined，并标记已 ensure', async () => {
    mockedListSpaces.mockResolvedValue({
      data: { items: [], total: 0, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    expect(mockedEnsurePersonal).toHaveBeenCalled(); // 无 PERSONAL 触发 ensure
    expect(useSpaceContextStore.getState().currentSpaceId).toBeUndefined();
    expect(useSpaceContextStore.getState().spaces).toEqual([]);
    expect(ls.getItem('tc_space_context_current_id')).toBeNull();
    expect(ls.getItem('tc_personal_space_ensured')).toBe('1'); // 标记已 ensure
  });

  it('init：接口失败时 setError，initialized 不置位（可重试）', async () => {
    mockedListSpaces.mockResolvedValueOnce({ error: { message: '加载失败', apiPath: '/spaces' } });
    await initSpaceContext();
    const s = useSpaceContextStore.getState();
    expect(s.error).toBe('加载失败');
    expect(s.loading).toBe(false);
    expect(s.currentSpaceId).toBeUndefined();
    expect(s.initialized).toBe(false);
    // 重试成功
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal], total: 1, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    expect(useSpaceContextStore.getState().currentSpaceId).toBe(10000);
    expect(useSpaceContextStore.getState().initialized).toBe(true);
  });

  it('init：幂等——initialized 后重复调用不再拉接口', async () => {
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal], total: 1, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    await initSpaceContext(); // 应跳过
    expect(mockedListSpaces).toHaveBeenCalledTimes(1);
  });

  it('refresh：init 之后仍可重拉最新列表，且保留仍有效的当前空间', async () => {
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal, team], total: 2, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    switchSpaceContext(10001);
    // 刷新时后端返回新增的团队空间
    const team2: Space = { ...team, spaceId: 10003, spaceCode: 't2', spaceName: '合规团队' };
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal, team, team2], total: 3, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await refreshSpaceContext();
    const s = useSpaceContextStore.getState();
    expect(mockedListSpaces).toHaveBeenCalledTimes(2); // init 1 次 + refresh 1 次（不受 initialized 幂等限制）
    expect(s.spaces.map((x) => x.spaceId)).toEqual([10000, 10001, 10003]);
    expect(s.currentSpaceId).toBe(10001); // 当前空间仍有效，保留
    expect(s.error).toBeUndefined();
    expect(s.loading).toBe(false);
  });

  it('refresh：当前空间被移出已加入列表时回落个人空间并修正持久化', async () => {
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal, team], total: 2, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    switchSpaceContext(10001);
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal], total: 1, page: 1, pageSize: 100, hasMore: false, warnings: [] }, // team 消失
    });
    await refreshSpaceContext();
    const s = useSpaceContextStore.getState();
    expect(s.spaces.map((x) => x.spaceId)).toEqual([10000]);
    expect(s.currentSpaceId).toBe(10000);
    expect(ls.getItem('tc_space_context_current_id')).toBe('10000');
  });

  it('refresh：接口失败时保留旧列表，仅置 error', async () => {
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal, team], total: 2, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    switchSpaceContext(10001);
    mockedListSpaces.mockResolvedValueOnce({ error: { message: '网络错误', apiPath: '/spaces' } });
    await refreshSpaceContext();
    const s = useSpaceContextStore.getState();
    expect(s.error).toBe('网络错误');
    expect(s.spaces.map((x) => x.spaceId)).toEqual([10000, 10001]); // 旧列表保留
    expect(s.currentSpaceId).toBe(10001); // 当前空间不丢
    expect(s.loading).toBe(false);
  });

  it('refresh：loading 中去重，不并发重拉', async () => {
    mockedListSpaces.mockResolvedValueOnce({
      data: { items: [personal], total: 1, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    let resolveSecond!: (v: Awaited<ReturnType<typeof adminService.listSpaces>>) => void;
    mockedListSpaces.mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveSecond = res;
        }),
    );
    const p1 = refreshSpaceContext();
    const p2 = refreshSpaceContext(); // loading 中，应被去重
    resolveSecond({
      data: { items: [personal], total: 1, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await Promise.all([p1, p2]);
    expect(mockedListSpaces).toHaveBeenCalledTimes(2); // init 1 次 + refresh 仅 1 次
  });

  it('switchSpace：更新 store + 写 localStorage', () => {
    useSpaceContextStore.getState().setSpaces([personal, team]);
    useSpaceContextStore.getState().setCurrentSpaceId(10000);
    switchSpaceContext(10001);
    expect(useSpaceContextStore.getState().currentSpaceId).toBe(10001);
    expect(useSpaceContextStore.getState().currentSpace?.spaceName).toBe('风控团队');
    expect(ls.getItem('tc_space_context_current_id')).toBe('10001');
  });
});

describe('页面刷新 / 重进管理区保持选中空间', () => {
  beforeEach(() => {
    ls.clear();
    useSpaceContextStore.getState().reset();
    mockedListSpaces.mockReset();
    mockedEnsurePersonal.mockReset();
    mockedEnsurePersonal.mockResolvedValue({ data: true });
  });

  it('切换为团队空间后重进管理区（init 幂等空转）应保持所选，不回退个人空间', async () => {
    mockedListSpaces.mockResolvedValue({
      data: { items: [personal, team], total: 2, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext(); // 进入管理区，无 localStorage → 默认个人空间
    switchSpaceContext(team.spaceId); // 用户切换到团队空间
    expect(useSpaceContextStore.getState().currentSpaceId).toBe(team.spaceId);
    expect(ls.getItem('tc_space_context_current_id')).toBe(String(team.spaceId));

    // 模拟「离开管理区再回来」：内存 store 不重置，AppShell 再次触发 init（幂等空转，不再重读 localStorage）
    await initSpaceContext();
    const s = useSpaceContextStore.getState();
    expect(mockedListSpaces).toHaveBeenCalledTimes(1); // 幂等，不重复拉接口
    expect(s.currentSpaceId).toBe(team.spaceId); // 保持用户所选，不被重置为个人空间
    expect(ls.getItem('tc_space_context_current_id')).toBe(String(team.spaceId)); // 持久化不被改写
  });

  it('页面刷新（内存 store 清零、localStorage 保留）后从 localStorage 恢复用户所选空间', async () => {
    mockedListSpaces.mockResolvedValue({
      data: { items: [personal, team], total: 2, page: 1, pageSize: 100, hasMore: false, warnings: [] },
    });
    await initSpaceContext();
    switchSpaceContext(team.spaceId); // 用户切换并持久化
    expect(ls.getItem('tc_space_context_current_id')).toBe(String(team.spaceId));

    // 模拟刷新：Zustand 内存状态清零（initialized=false），但 localStorage 保留用户选择
    useSpaceContextStore.getState().reset();
    await initSpaceContext(); // init 重新执行，从 localStorage 还原
    const s = useSpaceContextStore.getState();
    expect(s.currentSpaceId).toBe(team.spaceId); // 从 localStorage 恢复，保持用户所选（不回退个人空间）
    expect(ls.getItem('tc_space_context_current_id')).toBe(String(team.spaceId));
    expect(s.initialized).toBe(true);
  });
});

describe('ensurePersonalSpaceOnAppEntry', () => {
  // 单飞标记为模块级状态：isolateModules 取全新实例，各用例互不污染
  // （隔离注册表是全新求值，需在其中 require 被测模块及其 mock 的 adminService 引用；CVM 下不可用动态 import）。
  function freshModule() {
    jest.resetModules();
    let mod!: typeof import('@/hooks/useSpaceContext');
    let svc!: typeof import('@/services/admin').adminService;
    jest.isolateModules(() => {
      mod = require('@/hooks/useSpaceContext') as typeof import('@/hooks/useSpaceContext');
      svc = (require('@/services/admin') as typeof import('@/services/admin')).adminService;
    });
    return { mod, svc };
  }
  const mockedEnsure = (svc: typeof import('@/services/admin').adminService) =>
    svc.ensurePersonalSpace as unknown as jest.MockedFunction<typeof svc.ensurePersonalSpace>;

  it('进入项目即初始化个人空间一次（不等进入管理区域）', async () => {
    const { mod, svc } = await freshModule();
    mockedEnsure(svc).mockResolvedValue({ data: true });
    await mod.ensurePersonalSpaceOnAppEntry();
    expect(mockedEnsure(svc)).toHaveBeenCalledTimes(1);
  });

  it('单飞：同一页面加载内并发/顺序重复调用只发一次请求', async () => {
    const { mod, svc } = await freshModule();
    let resolveFirst!: (
      v: Awaited<ReturnType<typeof import('@/services/admin').adminService.ensurePersonalSpace>>,
    ) => void;
    mockedEnsure(svc).mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveFirst = res;
        }),
    );
    const p1 = mod.ensurePersonalSpaceOnAppEntry(); // 进行中即被单飞吸收
    const p2 = mod.ensurePersonalSpaceOnAppEntry();
    resolveFirst({ data: true });
    await Promise.all([p1, p2]);
    await mod.ensurePersonalSpaceOnAppEntry(); // 成功后重复调用仍不重发
    expect(mockedEnsure(svc)).toHaveBeenCalledTimes(1);
  });

  it('失败静默：不重抛，且本页不重试（进入管理区域时 initSpaceContext 的 ensure 分支兜底）', async () => {
    const { mod, svc } = await freshModule();
    mockedEnsure(svc).mockResolvedValue({ error: { message: '初始化失败', apiPath: '/spaces' } });
    await expect(mod.ensurePersonalSpaceOnAppEntry()).resolves.toBeUndefined();
    await mod.ensurePersonalSpaceOnAppEntry();
    expect(mockedEnsure(svc)).toHaveBeenCalledTimes(1);
  });
});
