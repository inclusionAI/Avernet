// 单聊树 re-export（botChatProvider/botSessionService）已移除（collab-form plan：barrel tightening）。
// 新增消费方一律按精确模块路径导入；单聊组件从 botChatProvider/botSessionService 直接引用。
export { ensureReactGlobal } from './ensureReactGlobal';
export * from './identityService';
export {
  ENABLE_TEST_USER,
  TEST_USER_IDENTITY,
  TEST_USER_IDENTITY_ID,
  TEST_USER_SUPPORT_TARGET_ID,
  isTestUserIdentity,
} from './testUser';
export * from './workspaceModel';
export * from './workspaceService';
