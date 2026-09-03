import { Button } from '@/components/ui/Button';
import type { TaskListItem } from '@/domain/tasks/models';
import { getCollaborationBotConversationUrl, getCollaborationGroupConversationUrl } from '@/utils/collaborationSquare';
import { history } from '@umijs/max';
import { MessageCircle } from 'lucide-react';

/**
 * 查看会话：按 session 形态 / 任务来源分流跳转路由。
 * - 协作群(source_type=coop_group 或 session 以 bcs_grp_ 开头) → tab=group 视图，
 *   groupId 优先取 source_group_id，缺失时从 bcs_grp session 截取前缀(workspace 亦可异步反查)。
 * - 单 bot → tab=chat 视图，需 owner_bot_id + owner_user_id 拼成 bot_id:user_id。
 */
export function ViewSessionButton({ record }: { record: TaskListItem }) {
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
  const conversationUrl = isGroupTask
    ? getCollaborationGroupConversationUrl(groupId, sessionId)
    : record.owner_bot_id && record.owner_user_id
    ? getCollaborationBotConversationUrl(`${record.owner_bot_id}:${record.owner_user_id}`, sessionId)
    : null;
  if (!conversationUrl) return null;

  return (
    <Button
      variant="secondary"
      size="sm"
      leftIcon={<MessageCircle className="size-4" />}
      onClick={() => history.push(conversationUrl)}
    >
      查看会话
    </Button>
  );
}
