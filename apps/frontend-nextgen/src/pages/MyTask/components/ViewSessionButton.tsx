import { Button } from '@/components/ui/Button';
import type { TaskListItem } from '@/domain/tasks/models';
import { getCollaborationBotConversationUrl, getCollaborationGroupConversationUrl } from '@/utils/collaborationSquare';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { history } from '@umijs/max';
import { MessageCircle } from 'lucide-react';

/**
 * 查看会话：按 session 形态 / 任务来源分流跳转路由。
 * - 协作群(source_type=coop_group 或 session 以 bcs_grp_ 开头) → 协作群页(/workspace/collaboration)，
 *   groupId 优先取 source_group_id，缺失时从 bcs_grp session 截取前缀(协作群页亦可异步反查)。
 * - 单 bot → 对话页(/workspace/chat)，按 owner_bot_id 从已管理 Bot 目录解析实际 owner user id，
 *   再拼成 bot_id:owner_user_id；任务 owner_user_id 实际是创建人，不参与路由。
 */
interface ViewSessionButtonProps {
  record: TaskListItem;
  label?: string;
  ownerBotUserIdMap: Record<string, string>;
}

function resolveSingleConversationBotTarget(
  ownerBotId: string,
  ownerBotUserIdMap: Record<string, string>,
): { botId: string; ownerId: string } | null {
  const rawBotId = ownerBotId.trim();
  if (!rawBotId) return null;

  const colonIndex = rawBotId.indexOf(':');
  const botId = colonIndex >= 0 ? rawBotId.slice(0, colonIndex).trim() : rawBotId;
  const embeddedOwnerId = colonIndex >= 0 ? rawBotId.slice(colonIndex + 1).trim() : '';
  const normalizedBotId = botId.toLowerCase();
  const normalizedFullBotId = rawBotId.toLowerCase();
  // Bot 目录优先；目录缺失时使用复合 owner_bot_id 内嵌的实际 owner（botId:ownerId）。
  const ownerId =
    ownerBotUserIdMap[normalizedFullBotId] ??
    (normalizedBotId ? ownerBotUserIdMap[normalizedBotId] : undefined) ??
    embeddedOwnerId;

  if (!botId || !ownerId?.trim()) return null;

  return { botId, ownerId: ownerId.trim() };
}

export function ViewSessionButton({ record, label = '查看会话', ownerBotUserIdMap }: ViewSessionButtonProps) {
  const sessionId =
    record.execution_config?.main_session_id?.trim() ||
    record.task_spec?.context?.extend_props?.teamclaw_context?.main_session_id?.trim();
  if (!sessionId) return null;

  // bcs_grp_<uuid>:<round> 形态 → 截取冒号前缀即 group_id。
  const groupIdFromSession = sessionId.startsWith('bcs_grp_')
    ? sessionId.lastIndexOf(':') > 0
      ? sessionId.slice(0, sessionId.lastIndexOf(':'))
      : sessionId
    : null;
  const groupId =
    record.execution_config?.source_group_id?.trim() ||
    record.task_spec?.context?.extend_props?.teamclaw_context?.source_group_id?.trim() ||
    groupIdFromSession;

  const isGroupTask = record.source_type === 'coop_group' || sessionId.startsWith('bcs_grp_');
  const singleBotTarget = isGroupTask ? null : resolveSingleConversationBotTarget(record.owner_bot_id, ownerBotUserIdMap);
  const conversationUrl = isGroupTask
    ? getCollaborationGroupConversationUrl(groupId, sessionId)
    : singleBotTarget
      ? getCollaborationBotConversationUrl(`${singleBotTarget.botId}:${singleBotTarget.ownerId}`, sessionId)
      : null;
  if (!conversationUrl) return null;

  return (
    <Button
      variant="secondary"
      size="sm"
      leftIcon={<MessageCircle className="size-4" />}
      onClick={() => {
        // 任务列表点击行携带焦点任务；目标会话按该任务而不是“最新任务”恢复 TaskPanel。
        useTaskSidePanelStore.getState().markTaskPanelOpenIntent(record.task_id);
        history.push(conversationUrl);
      }}
    >
      {label}
    </Button>
  );
}
