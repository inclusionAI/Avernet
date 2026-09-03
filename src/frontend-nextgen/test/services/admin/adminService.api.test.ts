/** @jest-environment node */
import { adminService } from '@/services/admin/adminService';
import * as spaceController from '@/services/backendApi/admin/spaceController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/admin/spaceController');
// ensureUserId 在 activeIdentityId 未就绪时补拉 identityService.loadIdentities；
// stub @tc-chat/adapters ESM transitive（identityService→testUser→supportProvider）。
jest.mock('@/services/workspace/identityService');
jest.mock('@tc-chat/adapters', () => ({}));

const sc = spaceController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.setState({ activeIdentityId: 'human_327325', identities: [] });
  // 命中缓存用例不调 loadIdentities；未就绪用例走 ensureUserId 补拉，默认失败降级为 error（不发业务请求）。
  (identityService.loadIdentities as unknown as jest.Mock<any>).mockResolvedValue({
    ok: false,
    error: { code: 'IDENTITY_LOAD_FAILED', friendlyMessage: '', canRetry: true },
  });
});

describe('adminService 网络参数对齐 clawweb=Avernet', () => {
  it('listSpaces 传 page_no + user_id（非 page）', async () => {
    sc.listSpaces.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
    await adminService.listSpaces({ page: 2, pageSize: 10, keyword: '风控' });
    expect(sc.listSpaces).toHaveBeenCalledWith({
      user_id: '327325',
      page_no: 2,
      page_size: 10,
      keyword: '风控',
    });
  });

  it('listSpaces 透传 scope=accessible（未传时不带 scope 键）', async () => {
    sc.listSpaces.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
    await adminService.listSpaces({ page: 1, pageSize: 100, scope: 'accessible' });
    expect(sc.listSpaces).toHaveBeenCalledWith({
      user_id: '327325',
      page_no: 1,
      page_size: 100,
      scope: 'accessible',
    });
    // 未传 scope 时不带该键（不影响其它调用方）
    sc.listSpaces.mockClear();
    await adminService.listSpaces({ page: 1, pageSize: 20 });
    expect(sc.listSpaces).toHaveBeenCalledWith({
      user_id: '327325',
      page_no: 1,
      page_size: 20,
    });
  });

  it('createTeamSpace 传 user_id + user_name(花名) query + body space_name', async () => {
    // 注入 identities 使 ensureUserName 经 getHumanIdentity 命中花名缓存（不调 loadIdentities）。
    useWorkspaceStore.setState({
      activeIdentityId: 'human_327325',
      identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
    });
    sc.createSpace.mockResolvedValue({
      success: true,
      data: { space_id: 1, space_name: '新团队', space_type: 'TEAM' },
    });
    await adminService.createTeamSpace({ spaceName: '新团队' });
    expect(sc.createSpace).toHaveBeenCalledWith({ space_name: '新团队' }, { user_id: '327325', user_name: '风太' });
  });

  it('createTeamSpace 取不到花名时不传 user_name（仅 user_id）', async () => {
    // identities 为空且 loadIdentities 默认失败 → ensureUserName 返回 null。
    useWorkspaceStore.setState({ activeIdentityId: 'human_327325', identities: [] });
    sc.createSpace.mockResolvedValue({
      success: true,
      data: { space_id: 1, space_name: '新团队', space_type: 'TEAM' },
    });
    await adminService.createTeamSpace({ spaceName: '新团队' });
    expect(sc.createSpace).toHaveBeenCalledWith({ space_name: '新团队' }, { user_id: '327325' });
  });

  it('addMember body 用 member_user_id + role=MEMBER，user_id 为操作者', async () => {
    sc.addSpaceMember.mockResolvedValue({ success: true, data: { user_id: 'u1', role: 'MEMBER' } });
    await adminService.addMember(10001, 'u1');
    expect(sc.addSpaceMember).toHaveBeenCalledWith(
      10001,
      { member_user_id: 'u1', role: 'MEMBER' },
      { user_id: '327325' },
    );
  });

  it('addMember 传 userName(花名) 时 body 带 member_user_name', async () => {
    sc.addSpaceMember.mockResolvedValue({ success: true, data: { user_id: 'u1', role: 'MEMBER' } });
    await adminService.addMember(10001, 'u1', 'MEMBER', '风太');
    expect(sc.addSpaceMember).toHaveBeenCalledWith(
      10001,
      { member_user_id: 'u1', role: 'MEMBER', member_user_name: '风太' },
      { user_id: '327325' },
    );
  });

  it('removeMember path=被删成员，user_id=操作者', async () => {
    sc.removeSpaceMember.mockResolvedValue({ success: true, data: { deleted: true } });
    await adminService.removeMember(10001, 'u1');
    expect(sc.removeSpaceMember).toHaveBeenCalledWith(10001, 'u1', { user_id: '327325' });
  });

  it('updateRole path=被改成员，body role，user_id=操作者', async () => {
    sc.updateMemberRole.mockResolvedValue({ success: true, data: { user_id: 'u1', role: 'ADMIN' } });
    await adminService.updateRole(10001, 'u1', 'ADMIN');
    expect(sc.updateMemberRole).toHaveBeenCalledWith(10001, 'u1', { role: 'ADMIN' }, { user_id: '327325' });
  });

  it('requestJoin 传 user_id + user_name(花名) query + body reason', async () => {
    // 注入 identities 使 ensureUserName 经 getHumanIdentity 命中花名缓存（不调 loadIdentities）。
    useWorkspaceStore.setState({
      activeIdentityId: 'human_327325',
      identities: [{ id: 'human_327325', kind: 'user', displayName: '风太', online: true }],
    });
    sc.requestJoinSpace.mockResolvedValue({ success: true, data: {} });
    await adminService.requestJoin(10001, '希望加入');
    expect(sc.requestJoinSpace).toHaveBeenCalledWith(
      10001,
      { reason: '希望加入' },
      { user_id: '327325', user_name: '风太' },
    );
  });

  it('requestJoin 取不到花名时不传 user_name（仅 user_id）', async () => {
    // identities 为空且 loadIdentities 默认失败 → ensureUserName 返回 null。
    useWorkspaceStore.setState({ activeIdentityId: 'human_327325', identities: [] });
    sc.requestJoinSpace.mockResolvedValue({ success: true, data: {} });
    await adminService.requestJoin(10001, '希望加入');
    expect(sc.requestJoinSpace).toHaveBeenCalledWith(10001, { reason: '希望加入' }, { user_id: '327325' });
  });

  it('listMembers 传 page_no + user_id', async () => {
    sc.listSpaceMembers.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
    await adminService.listMembers(10001, { page: 1, pageSize: 20 });
    expect(sc.listSpaceMembers).toHaveBeenCalledWith(
      10001,
      expect.objectContaining({ user_id: '327325', page_no: 1, page_size: 20 }),
    );
  });

  it('activeIdentityId 未就绪时返回 error 不发请求', async () => {
    useWorkspaceStore.setState({ activeIdentityId: null });
    const r = await adminService.listSpaces();
    expect(r.error).toBeDefined();
    expect(sc.listSpaces).not.toHaveBeenCalled();
  });

  it('deleteSpace 仍可调用（UI 入口已隐藏，代码保留待后端补）', async () => {
    sc.deleteSpace.mockResolvedValue({ success: true, data: { deleted: true } });
    const r = await adminService.deleteSpace(10001);
    expect(sc.deleteSpace).toHaveBeenCalledWith(10001);
    expect(r.data).toBe(true);
  });
});

// 失败时透传后端 message（HTTP 2xx + code!==200000 业务失败，以及 5xx 抛错经 body.message），
// 不再用「创建失败/添加失败/修改失败」等操作类型预设覆盖；并保留 request_id 供排障。
describe('adminService 失败透传后端 message', () => {
  it('createTeamSpace：业务失败(code!==200000)透传后端 message，不用「创建失败」覆盖', async () => {
    sc.createSpace.mockResolvedValue({
      code: 502201,
      message: 'Skill Center team creation failed',
      data: null,
      request_id: 'rid-create',
    });
    const r = await adminService.createTeamSpace({ spaceName: '新团队' });
    expect(r.error).toBeDefined();
    expect(r.error?.message).toBe('Skill Center team creation failed');
    expect(r.error?.message).not.toContain('未返回空间数据');
    expect(r.error?.requestId).toBe('rid-create');
  });

  it('createTeamSpace：5xx 抛错时透传 body.message（经 BackendRequestError.data，不被「服务器暂时不可用」覆盖）', async () => {
    sc.createSpace.mockRejectedValue(
      new BackendRequestError('服务器暂时不可用，请稍后重试', {
        status: 502,
        data: {
          code: 502201,
          message: 'Skill Center team creation failed',
          data: null,
          request_id: 'rid-502',
        },
        apiPath: '/openapi/v1/bots/spaces/create',
      }),
    );
    const r = await adminService.createTeamSpace({ spaceName: '新团队' });
    expect(r.error?.message).toBe('Skill Center team creation failed');
    expect(r.error?.message).not.toContain('服务器暂时不可用');
    expect(r.error?.requestId).toBe('rid-502');
  });

  it('addMember：业务失败透传后端 message，不用「添加失败」覆盖', async () => {
    sc.addSpaceMember.mockResolvedValue({
      code: 502201,
      message: '成员数量已达上限',
      data: null,
      request_id: 'rid-add',
    });
    const r = await adminService.addMember(10001, 'u1');
    expect(r.error?.message).toBe('成员数量已达上限');
    expect(r.error?.message).not.toContain('添加失败');
    expect(r.error?.requestId).toBe('rid-add');
  });

  it('updateRole：业务失败透传后端 message，不用「修改失败」覆盖', async () => {
    sc.updateMemberRole.mockResolvedValue({
      code: 502201,
      message: '无权修改他人角色',
      data: null,
      request_id: 'rid-role',
    });
    const r = await adminService.updateRole(10001, 'u1', 'ADMIN');
    expect(r.error?.message).toBe('无权修改他人角色');
    expect(r.error?.message).not.toContain('修改失败');
  });
});
