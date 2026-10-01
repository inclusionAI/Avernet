/** @jest-environment node */
// 批量添加成员：Promise.allSettled 聚合 + 按 envelope {error} 归属成功/失败。
// 关键（design D2）：adminService.addMember 业务失败是 resolve({error}) 而非 reject；
// 朴素「按 rejected 判失败」会把业务失败静默误判为成功——本用例钉死这一陷阱。
import { getCapabilities } from '@/capabilities';
import { adminService } from '@/services/admin/adminService';
import * as spaceController from '@/services/backendApi/admin/spaceController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { identityService } from '@/services/workspace/identityService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/admin/spaceController');
jest.mock('@/capabilities');
jest.mock('@/services/workspace/identityService');
jest.mock('@tc-chat/adapters', () => ({}));

const sc = spaceController as unknown as Record<string, jest.Mock<any>>;
const getCapabilitiesMock = getCapabilities as unknown as jest.Mock;

/** 固定能力：恒返回给定操作者身份（null = 未就绪）。默认 userId-only（不落 user_name）。 */
function setUserIdentity(value: { userId: string; displayName?: string | null } | null): void {
  const capVal = value ? { userId: value.userId, displayName: value.displayName ?? null } : null;
  getCapabilitiesMock.mockReturnValue({
    getHumanIdentity: () => ({ status: 'available', value: capVal }),
  });
}

beforeEach(() => {
  jest.resetAllMocks();
  (identityService.loadIdentities as unknown as jest.Mock<any>).mockResolvedValue({
    ok: false,
    error: { code: 'IDENTITY_LOAD_FAILED', friendlyMessage: '', canRetry: true },
  });
  setUserIdentity({ userId: '900003', displayName: null });
});

/**
 * 按 member_user_id 分派单发 addSpaceMember 的返回：
 *  - resolve 一个信封（success envelope 或业务失败 envelope）；
 *  - reject 一个 BackendRequestError（网络异常，且可控制 message 是否可读以验证「请求异常」回退）。
 */
function mockAddByUserId(
  map: Record<string, { resolve?: Record<string, unknown> | null; reject?: BackendRequestError } | null>,
): void {
  sc.addSpaceMember.mockImplementation((_spaceId: unknown, body: { member_user_id: string }) => {
    const entry = map[body.member_user_id];
    if (!entry || entry.resolve === null || entry.reject) {
      // reject：无 entry 视为「无可读 message 的 500」→ 验证「请求异常」回退
      const err =
        entry?.reject ??
        new BackendRequestError('', { status: 500, data: {}, apiPath: '/openapi/v1/bots/spaces/10001/members' });
      return Promise.reject(err);
    }
    return Promise.resolve(entry.resolve);
  });
}

describe('adminService.addMembersBatch：Promise.allSettled + {error} 归属', () => {
  it('全部成功 → succeeded 含全部、failed 为空', async () => {
    mockAddByUserId({
      ok1: { resolve: { success: true, data: { user_id: 'ok1', role: 'MEMBER' } } },
      ok2: { resolve: { success: true, data: { user_id: 'ok2', role: 'MEMBER' } } },
      ok3: { resolve: { success: true, data: { user_id: 'ok3', role: 'MEMBER' } } },
    });
    const r = await adminService.addMembersBatch(
      10001,
      [{ userId: 'ok1' }, { userId: 'ok2' }, { userId: 'ok3' }],
      'MEMBER',
    );
    expect(r.succeeded.map((m) => m.userId).sort()).toEqual(['ok1', 'ok2', 'ok3']);
    expect(r.failed).toEqual([]);
    expect(sc.addSpaceMember).toHaveBeenCalledTimes(3);
  });

  it('混合：业务失败（已是成员 resolve{error}）与成功并存 → 业务失败计入 failed 不被静默吞为成功', async () => {
    // ★ D2 陷阱钉死：already 是 resolve({error}) 的业务失败，必须出现在 failed 而非 succeeded
    mockAddByUserId({
      ok: { resolve: { success: true, data: { user_id: 'ok', role: 'MEMBER' } } },
      already: { resolve: { code: 502201, message: '已是成员', data: null, request_id: 'rid-already' } },
    });
    const r = await adminService.addMembersBatch(
      10001,
      [{ userId: 'ok' }, { userId: 'already', userName: '花名A' }],
      'MEMBER',
    );
    expect(r.succeeded.map((m) => m.userId)).toEqual(['ok']);
    expect(r.failed).toHaveLength(1);
    expect(r.failed[0]).toMatchObject({ userId: 'already', userName: '花名A', reason: '已是成员' });
    // 关键反向断言：业务失败绝不被误算进 succeeded
    expect(r.succeeded.find((m) => m.userId === 'already')).toBeUndefined();
  });

  it('网络异常无可读 message → 失败原因回退「请求异常」', async () => {
    // 5xx + 空 body + 空 message → toServiceError 得 message='' → 聚合回退「请求异常」
    mockAddByUserId({
      neterr: {
        reject: new BackendRequestError('', {
          status: 500,
          data: {},
          apiPath: '/openapi/v1/bots/spaces/10001/members',
        }),
      },
    });
    const r = await adminService.addMembersBatch(10001, [{ userId: 'neterr' }], 'MEMBER');
    expect(r.succeeded).toEqual([]);
    expect(r.failed[0]).toMatchObject({ userId: 'neterr', reason: '请求异常' });
  });

  it('网络异常带可读 message → 透传该 message 而非「请求异常」', async () => {
    mockAddByUserId({
      neterr: {
        reject: new BackendRequestError('网关超时', {
          status: 502,
          data: { code: 502201, message: '网关超时', data: null, request_id: 'rid-502' },
          apiPath: '/openapi/v1/bots/spaces/10001/members',
        }),
      },
    });
    const r = await adminService.addMembersBatch(10001, [{ userId: 'neterr' }], 'MEMBER');
    expect(r.failed[0].reason).toBe('网关超时');
  });

  it('全部失败 → succeeded 为空、failed 全员', async () => {
    mockAddByUserId({
      already: { resolve: { code: 502201, message: '已是成员', data: null, request_id: 'r1' } },
      noperm: { resolve: { code: 502201, message: '无权限', data: null, request_id: 'r2' } },
    });
    const r = await adminService.addMembersBatch(10001, [{ userId: 'already' }, { userId: 'noperm' }], 'ADMIN');
    expect(r.succeeded).toEqual([]);
    expect(r.failed.map((f) => f.reason)).toEqual(['已是成员', '无权限']);
  });

  it('每项 member_user_name 逐项透传到单发 body（不共享、不丢失）', async () => {
    mockAddByUserId({
      named: { resolve: { success: true, data: { user_id: 'named', role: 'MEMBER' } } },
      plain: { resolve: { success: true, data: { user_id: 'plain', role: 'MEMBER' } } },
    });
    await adminService.addMembersBatch(10001, [{ userId: 'named', userName: '花名甲' }, { userId: 'plain' }], 'MEMBER');
    const calls = sc.addSpaceMember.mock.calls as unknown as [
      unknown,
      Record<string, unknown>,
      Record<string, unknown>,
    ][];
    const namedCall = calls.find((c) => c[1]?.member_user_id === 'named');
    const plainCall = calls.find((c) => c[1]?.member_user_id === 'plain');
    expect(namedCall?.[1]).toMatchObject({ member_user_id: 'named', member_user_name: '花名甲', role: 'MEMBER' });
    expect(plainCall?.[1]).toMatchObject({ member_user_id: 'plain', role: 'MEMBER' });
    expect(plainCall?.[1]).not.toHaveProperty('member_user_name');
  });

  it('role 对整批生效（每单发 body 均带同一 role）', async () => {
    mockAddByUserId({
      a: { resolve: { success: true, data: { user_id: 'a', role: 'ADMIN' } } },
      b: { resolve: { success: true, data: { user_id: 'b', role: 'ADMIN' } } },
    });
    await adminService.addMembersBatch(10001, [{ userId: 'a' }, { userId: 'b' }], 'ADMIN');
    const calls = sc.addSpaceMember.mock.calls as unknown as [
      unknown,
      Record<string, unknown>,
      Record<string, unknown>,
    ][];
    expect(calls.every((c) => c[1]?.role === 'ADMIN')).toBe(true);
    expect(calls.every((c) => c[2]?.user_id === '900003')).toBe(true);
  });

  it('空批次 → 不发请求、succeeded/failed 均为空', async () => {
    const r = await adminService.addMembersBatch(10001, [], 'MEMBER');
    expect(r.succeeded).toEqual([]);
    expect(r.failed).toEqual([]);
    expect(sc.addSpaceMember).not.toHaveBeenCalled();
  });

  it('不抛异常：即使含 reject，整体仍 resolve（失败收口到 failed，不 reject）', async () => {
    mockAddByUserId({
      neterr: { reject: new BackendRequestError('', { status: 500, data: {}, apiPath: '/x' }) },
    });
    await expect(adminService.addMembersBatch(10001, [{ userId: 'neterr' }], 'MEMBER')).resolves.toBeDefined();
  });
});
