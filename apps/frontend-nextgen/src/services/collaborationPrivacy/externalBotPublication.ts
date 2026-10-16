/**
 * 外部 Bot 对 Bot 可见性直改路由（bcs-frontend-external-bot-config）。
 *
 * TC Bot 走 publishBotPublic 审批链路的行为保持不变；外部 Bot 合同只允许
 * PATCH /openapi/v1/collaboration/bots/{botId}（复用 apiAdapter 的业务码 + DTO 运行时校验）：
 * - 全部可见 body 固定为 {visibility:'public'}，不可见 {visibility:'private'}；
 * - 限定组织先 GET 同一 Bot 取最新 friend_ext，整体合并后只替换 view_scope_agent_friend_deps，
 *   值为组织完整名称字符串（BCS 按名称精确/前缀匹配），绝不用部门编码；
 * - 无审批语义：不提取审批链接，成功即已完成（completed）。
 */
import {
  applyExternalViewScopeToBot,
  buildExternalViewScopeFriendExt,
} from '@/domain/collaborationPrivacy/externalBotVisibility';
import type { CollaborationBot } from '@/domain/collaborationPrivacy/types';
import type { CollaborationBotDto } from '@/services/backendApi';
import type { CollaborationPrivacyApiAdapter } from './collaborationPrivacyApiAdapter';
import type { PublicationCommand, PublicationResult } from './collaborationPrivacyGateway';

/** 从提交指令解析部门完整名称：优先编辑器 deptEntries 的 deptName，回退到已归一的组织路径。 */
function readCommandDepartmentNames(command: PublicationCommand): string[] {
  const entryNames = (command.deptEntries ?? []).map((entry) => entry.deptName.trim()).filter(Boolean);
  const source = entryNames.length
    ? entryNames
    : command.config.organizationPaths.map((path) => path.join(' / ').trim()).filter(Boolean);
  return [...new Set(source)];
}

/**
 * 外部 Bot 通用配置弹窗加载归一：对 Bot 可见性按外部合同重建
 * （public 忽略陈旧部门范围；protected 名称字符串原样回显；清空审批态）。
 */
export function applyExternalBotDtoToDomain(bot: CollaborationBot, dto: CollaborationBotDto): CollaborationBot {
  return applyExternalViewScopeToBot(bot, { visibility: dto.visibility, friend_ext: dto.friend_ext });
}

/**
 * 外部 Bot 的部门范围是名称字符串而不是部门编码，跳过按 deptNo 的回显查询，
 * 避免为名称发起注定失败的组织搜索。
 */
export function stripExternalAgentViewScopeDeps(dto: CollaborationBotDto): CollaborationBotDto {
  const friendExt = dto.friend_ext;
  if (!friendExt || !Array.isArray(friendExt.view_scope_agent_friend_deps)) return dto;
  const restFriendExt: Record<string, unknown> = { ...friendExt };
  delete restFriendExt.view_scope_agent_friend_deps;
  return { ...dto, friend_ext: restFriendExt };
}

/** 外部 Bot 直改结果：完成态配置 + 已通过业务码与 DTO 校验的响应（供快照刷新使用）。 */
export interface ExternalPublicationWrite {
  result: PublicationResult;
  patchedBot: CollaborationBotDto;
}

/** 外部 Bot 提交：审批链路之外的显式直改路由。 */
export async function submitExternalPublication(
  apiAdapter: CollaborationPrivacyApiAdapter,
  command: PublicationCommand,
  signal?: AbortSignal,
): Promise<ExternalPublicationWrite> {
  if (command.audience !== 'bot') {
    throw new Error('外部 Bot 仅支持配置对 Bot 可见性');
  }
  let patchedBot: CollaborationBotDto;
  if (command.config.scope === 'restricted') {
    const names = readCommandDepartmentNames(command);
    if (names.length === 0) {
      throw new Error('组织范围缺少部门名称，无法保存 Bot 可见性');
    }
    // friend_ext 是顶层整体替换语义，必须先取最新值再合并，保留全部未知键。
    const current = await apiAdapter.getManagedBot(command.botId, signal);
    const friendExt = buildExternalViewScopeFriendExt(current.friend_ext, names);
    patchedBot = await apiAdapter.patchManagedBot(command.botId, { visibility: 'protected', friend_ext: friendExt }, signal);
  } else {
    const visibility = command.config.scope === 'none' ? 'private' : 'public';
    patchedBot = await apiAdapter.patchManagedBot(command.botId, { visibility }, signal);
  }
  return { result: { status: 'completed', config: structuredClone(command.config) }, patchedBot };
}