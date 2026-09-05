import { TEST_USER_SUPPORT_TARGET_ID } from '@/services/workspace';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';

/** 测试用户身份下的客服测试会话 target（isTestUser 时 botChatTarget 兜底返回）。 */
export const TEST_SUPPORT_TARGET: ConversationTarget = {
  id: TEST_USER_SUPPORT_TARGET_ID,
  name: '客服测试',
  avatar: 'TS',
  engine: 'OpenClaw',
  group: 'mine',
  status: 'available',
  summary: '测试客服',
  kind: 'single',
};
