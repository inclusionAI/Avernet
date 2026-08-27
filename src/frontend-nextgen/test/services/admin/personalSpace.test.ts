// 个人空间幂等确保：adminService.ensurePersonalSpace 注入 user_id 并调 initializePersonalSpace。
// 缓存标记（localStorage）逻辑在 useSpaceContext，此测试聚焦 Service→Controller 调用契约。
import { adminService } from '@/services/admin/adminService';
import * as wc from '@/services/backendApi';
import { useWorkspaceStore } from '@/stores/workspaceStore';

jest.mock('@/services/backendApi');

describe('adminService.ensurePersonalSpace', () => {
  beforeEach(() => {
    useWorkspaceStore
      .getState()
      .setIdentities([{ id: 'user:146836', kind: 'user', displayName: '我', online: true }], 'user:146836');
    jest.clearAllMocks();
  });

  it('成功 → data:true（调 initializePersonalSpace 注入 user_id + user_name(花名)）', async () => {
    (wc.initializePersonalSpace as jest.Mock).mockResolvedValue({ code: 200000, data: {} });
    const r = await adminService.ensurePersonalSpace();
    expect(r.data).toBe(true);
    expect(wc.initializePersonalSpace).toHaveBeenCalledWith({ user_id: '146836', user_name: '我' });
  });

  it('后端失败 → error 不抛出、不阻断', async () => {
    (wc.initializePersonalSpace as jest.Mock).mockRejectedValue(new Error('500'));
    const r = await adminService.ensurePersonalSpace();
    expect(r.data).toBeUndefined();
    expect(r.error).toBeDefined();
    expect(r.error?.message).toContain('500');
  });

  it('无身份 → 错误降级，不调后端', async () => {
    useWorkspaceStore.getState().setIdentities([], '');
    const r = await adminService.ensurePersonalSpace();
    expect(r.error).toBeDefined();
    expect(wc.initializePersonalSpace).not.toHaveBeenCalled();
  });
});
