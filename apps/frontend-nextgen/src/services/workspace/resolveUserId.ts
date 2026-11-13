import { resolveOpenApiUserId } from '@/domain/userIdentity';

/** user_id 规则:human 身份 id 含冒号取尾段,否则原值。 */
/** mine 返回的 human 身份 bot_id 形如 "human_900003",工号取 "human_" 之后的部分;
 *  兼容旧的 "{head}:{staffNo}" 复合写法(取首个冒号之后)。结果只保留工号本身。
 *  自 botSessionService 独立成模块（collab-form spec §4）:调用 /openapi/v1/bots/* 的
 *  各服务按精确路径复用,不拖入整个单聊会话服务。 */
export function resolveUserId(userId: string): string {
  return resolveOpenApiUserId(userId);
}
