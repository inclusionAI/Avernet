// 角色列禁用态与提示文案纯逻辑测试（对齐 PRD tooltip 文案矩阵）。
// PRD 场景：个人空间 / 未加入团队 / 非管理员 / 最后一位管理员；teamclaw 增量：创建者保护。
import { getRoleState, type RoleCellValue } from '@/components/Admin/SpaceMemberList/SpaceMemberRow';
import type { Space, SpaceMember } from '@/domain/admin/models';

const teamSpace = (over: Partial<Space> = {}): Space =>
  ({
    spaceId: 1,
    spaceCode: 'S1',
    spaceName: '团队',
    spaceType: 'TEAM',
    currentUserRole: 'ADMIN',
    ownerCount: 1,
    memberCount: 2,
    botCount: 0,
    gmtModified: '',
    ...over,
  } as Space);

const member = (over: Partial<SpaceMember> = {}): SpaceMember =>
  ({
    userId: 'u1',
    userName: '张三',
    role: 'MEMBER',
    botPermissionCount: 0,
    isCreator: false,
    gmtModified: '',
    ...over,
  } as SpaceMember);

describe('getRoleState 角色列禁用态文案（对齐 PRD）', () => {
  it('个人空间 → 禁用 + "个人空间不可修改角色"', () => {
    expect(getRoleState(teamSpace({ spaceType: 'PERSONAL' }), member(), true, false)).toEqual({
      disabled: true,
      reason: '个人空间不可修改角色',
    });
  });

  it('未加入团队 → 禁用 + "未加入的团队空间不可操作"', () => {
    const s = teamSpace({ currentUserRole: undefined });
    expect(getRoleState(s, member(), false, false)).toEqual({
      disabled: true,
      reason: '未加入的团队空间不可操作',
    });
  });

  it('已加入但当前用户非管理员 → 禁用 + "仅管理员可修改角色"', () => {
    expect(getRoleState(teamSpace({ currentUserRole: 'MEMBER' }), member(), false, false)).toEqual({
      disabled: true,
      reason: '仅管理员可修改角色',
    });
  });

  it('创建者 → 禁用 + "创建者不可变更角色"（teamclaw 保留保护）', () => {
    expect(getRoleState(teamSpace(), member({ isCreator: true }), true, false)).toEqual({
      disabled: true,
      reason: '创建者不可变更角色',
    });
  });

  it('最后一位管理员 → 禁用 + "至少需保留一位管理员"', () => {
    expect(getRoleState(teamSpace(), member({ role: 'ADMIN' }), true, true)).toEqual({
      disabled: true,
      reason: '至少需保留一位管理员',
    });
  });

  it('普通可改成员 → 启用 + 无提示', () => {
    expect(getRoleState(teamSpace(), member({ role: 'MEMBER' }), true, false)).toEqual({
      disabled: false,
      reason: '',
    });
  });

  it('非最后一位管理员 → 启用（管理员降级不被 lastOwner 拦截）', () => {
    expect(getRoleState(teamSpace(), member({ role: 'ADMIN' }), true, false)).toEqual({
      disabled: false,
      reason: '',
    });
  });
});

// 编译期类型校验：RoleCellValue 仅 ADMIN/MEMBER
const _t: RoleCellValue = 'ADMIN';
void _t;
