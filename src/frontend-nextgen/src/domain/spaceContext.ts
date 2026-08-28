// 全局空间上下文（Space Context）：当前工作空间 + 可切换空间列表。
// Open Core 通用能力，供 Bot 工坊 / 能力工坊 / 能力市场等模块按 space_id 查询。
// 设计依据：docs/specs/2026-08-17-global-space-context-switcher/spec.md §7、plan.md §2。
import type { Space } from '@/domain/admin/models';

/** 全局空间上下文状态；spaceContextStore 按 this shape 承载。 */
export interface SpaceContextState {
  /** 当前选中空间 id；未初始化或无空间时为 undefined */
  currentSpaceId: number | undefined;
  /** 当前空间完整对象（便于 UI 直接渲染名称/类型）；由 currentSpaceId 在已加入子集中推算 */
  currentSpace: Space | undefined;
  /** 可切换空间列表（仅当前账号已加入：个人空间 + 已加入团队空间） */
  spaces: Space[];
  loading: boolean;
  error?: string;
}

/**
 * 从 /openapi/v1/spaces 返回的可见全集中过滤出「当前账号已加入」的空间。
 * 判定（双判兼容后端字段缺失）：joinStatus 为 JOINED，或 currentUserRole 为 ADMIN/MEMBER。
 * 排除 AVAILABLE（可申请）与 APPLIED（申请中）的团队空间。
 */
export function filterJoinedSpaces(spaces: Space[]): Space[] {
  return spaces.filter((s) => {
    if (s.joinStatus === 'JOINED') return true;
    const role = s.currentUserRole;
    return role === 'ADMIN' || role === 'MEMBER';
  });
}

/**
 * 在已加入子集中取默认当前空间（初始化/无可还原 id 时调用）。
 * 优先级：个人空间 → 第一个已加入工作空间（团队）→ undefined（无空间则不选中）。
 * 每账号有且仅有一个个人空间（joinStatus 天然 JOINED），取第一个 PERSONAL；
 * 个人空间缺失时回落 spaces[0]（首个已加入团队空间），保证有空间时总有默认选中。
 */
export function pickDefaultSpace(spaces: Space[]): Space | undefined {
  const personal = spaces.find((s) => s.spaceType === 'PERSONAL');
  if (personal) return personal;
  return spaces[0];
}

/**
 * 空间列表排序：个人空间排前，团队空间按创建时间倒序（新→旧）。
 * 需求：列表页个人空间（当前账号唯一的）排在所有团队空间之前；团队空间按 gmtCreate 降序。
 * 稳定排序：同类型同时间保持原序；缺失 gmtCreate 回退最小值排末尾。
 */
/** 是否当前账号有权限（已加入）的团队空间：joinStatus=JOINED 或 current_user_role=ADMIN/MEMBER。 */
function isJoinedTeam(s: Space): boolean {
  if (s.joinStatus === 'JOINED') return true;
  const role = s.currentUserRole;
  return role === 'ADMIN' || role === 'MEMBER';
}

/**
 * 空间列表排序，优先级从高到低：
 * 1. 个人空间（当前账号唯一的，排前）
 * 2. 有权限的团队空间（已加入：joinStatus=JOINED 或角色 ADMIN/MEMBER）
 * 3. 其它团队空间（未加入，可申请）—— 按 gmtCreate 倒序（新→旧）
 * 同层级内按 gmtCreate 倒序保持稳定；缺失 gmtCreate 排末尾；个人空间保持原序。
 */
export function sortSpacesByDisplayOrder<T extends Space>(spaces: T[]): T[] {
  const tier = (s: T): 0 | 1 | 2 => {
    if (s.spaceType === 'PERSONAL') return 0;
    return isJoinedTeam(s) ? 1 : 2;
  };
  const cmpTimeDesc = (a: T, b: T): number => {
    const ta = a.gmtCreate ?? '';
    const tb = b.gmtCreate ?? '';
    return tb < ta ? -1 : tb > ta ? 1 : 0;
  };
  return [...spaces].sort((a, b) => {
    const ta = tier(a);
    const tb = tier(b);
    if (ta !== tb) return ta - tb;
    // 个人空间层级不按时间排（唯一），保持原序；团队层级内按 gmtCreate 倒序
    return ta === 0 ? 0 : cmpTimeDesc(a, b);
  });
}
