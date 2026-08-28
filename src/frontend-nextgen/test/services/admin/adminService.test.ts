import type { Space } from '@/domain/admin/models';
import { adminService } from '@/services/admin/adminService';
import { describe, expect, it, jest } from '@jest/globals';

// adminService 经 ensureUserId→identityService→testUser→supportProvider transitive 引入
// @tc-chat/adapters（node 环境 ESM），stub 掉仅满足模块解析。
jest.mock('@tc-chat/adapters', () => ({}));

const base = (over: Partial<Space>): Space => ({
  spaceId: 1,
  spaceCode: 's',
  spaceName: 'x',
  spaceType: 'TEAM',
  botCount: 0,
  memberCount: 0,
  ownerCount: 0,
  gmtModified: '',
  ...over,
});

describe('adminService.canManage', () => {
  it('allows manage when current_user_role is ADMIN', () => {
    expect(adminService.canManage(base({ currentUserRole: 'ADMIN' }))).toEqual({ ok: true });
  });
  it('blocks manage for MEMBER role', () => {
    const r = adminService.canManage(base({ currentUserRole: 'MEMBER' }));
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/管理员/);
  });
  it('blocks manage when not a member (no role)', () => {
    const r = adminService.canManage(base({}));
    expect(r.ok).toBe(false);
  });
});

describe('adminService.canViewMembers', () => {
  it('allows viewing when current_user_role is ADMIN', () => {
    expect(adminService.canViewMembers(base({ currentUserRole: 'ADMIN' }))).toEqual({ ok: true });
  });
  it('allows viewing when current_user_role is MEMBER', () => {
    expect(adminService.canViewMembers(base({ currentUserRole: 'MEMBER' }))).toEqual({ ok: true });
  });
  it('allows viewing the personal space owner (isCreator) even without role', () => {
    expect(adminService.canViewMembers(base({ spaceType: 'PERSONAL', isCreator: true }))).toEqual({ ok: true });
  });
  it('blocks viewing when not a member (no role, not creator)', () => {
    const r = adminService.canViewMembers(base({}));
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/暂无权限|加入/);
  });
  it('blocks viewing when role is UNKNOWN (treated as not joined)', () => {
    const r = adminService.canViewMembers(base({ currentUserRole: 'UNKNOWN' }));
    expect(r.ok).toBe(false);
  });
  it('blocks viewing a personal space the user does not own', () => {
    const r = adminService.canViewMembers(base({ spaceType: 'PERSONAL' }));
    expect(r.ok).toBe(false);
  });
});

describe('adminService.getOverview', () => {
  it('returns a stable overview object', () => {
    const o = adminService.getOverview();
    expect(o.module).toBe('admin');
    expect(o.description).toBeTruthy();
  });
});
