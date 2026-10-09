import type { IdentityView } from '@/domain/collaboration';
import { TEST_USER_IDENTITY_ID } from '@/domain/collaboration/availableViews';
import { TEAMCLAW_SUPPORT_BOT } from '../supportProvider';

/** 「测试用户」示例身份开关。下线时置 false(或删模块并移除 identityService 注入一行)。 */
export const ENABLE_TEST_USER = false;
export { TEST_USER_IDENTITY_ID }; // re-export,便于调用方就近 import
export const TEST_USER_SUPPORT_TARGET_ID = TEAMCLAW_SUPPORT_BOT.targetId;

export const TEST_USER_IDENTITY: IdentityView = {
  id: TEST_USER_IDENTITY_ID,
  kind: 'user',
  displayName: '测试用户',
  avatarUrl: undefined,
  online: true,
  status: 'online',
  reachability: 'reachable',
};

export function isTestUserIdentity(id: string | null | undefined): boolean {
  return !!id && id === TEST_USER_IDENTITY_ID;
}
