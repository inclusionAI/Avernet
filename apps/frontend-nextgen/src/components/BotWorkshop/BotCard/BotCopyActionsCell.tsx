import type { BotCopyStateSummary, BotDomain } from '@/domain/botWorkshop';
import type { BotCardManagementAction } from './config';
import BotCopyDeleteAction from './BotCopyDeleteAction';
import BotCopyRetryAction from './BotCopyRetryAction';

/**
 * copy_state 非空时的操作列收敛:dst bot 尚在复制中(中间态)或已失败。
 * view/edit/chat/logs/三点菜单等运行态操作此刻都打不到真目标,一律隐藏;
 * FAILED 且有 retry → 留重试;删除按钮仅在 dst 已 ACTIVE(lifecycle=running)时展示 ——
 * 中间态 dst 尚未起,删除无意义;FAILED 必然 ACTIVE。删除复用 onAction('delete')
 * + 原确认弹窗(BotManagementConfirmDialog)。开关由调用方 BOT_COPY_FEATURE_ENABLED 把守,
 * 组件本身不读开关。
 *
 * owner 门控:dst bot 是 owner 私有的复制中间态产物,重试/删除仅当前用户为 owner 时显示;
 * 非 owner 两按钮均不渲染(操作列为空)。owner 严格口径同 useBotWorkshop.canRestartContainer
 * (bot.ownerId === currentOpenApiUserId),不放宽到 bot.actions.includes('edit');后端
 * retry/delete 仍持 userScopedParams + 既有鉴权兜底。currentUserId 缺省(身份未就绪)保守为非 owner,
 * 与 canRestartContainer 在 currentOpenApiUserId 为 undefined 时返回 false 一致。
 */
export function BotCopyActionsCell({
  bot,
  copyState,
  onRetryCopy,
  onAction,
  currentUserId,
}: {
  bot: BotDomain;
  copyState: BotCopyStateSummary;
  onRetryCopy?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  currentUserId?: string;
}) {
  const isOwner = Boolean(currentUserId) && bot.ownerId === currentUserId;
  // dst 已 ACTIVE 才能删除(中间态 dst 尚未起,删无意义);FAILED 必然 ACTIVE;仅 owner 可删。
  const deleteAction = bot.lifecycle === 'running' && isOwner ? onAction : undefined;
  const showRetry = copyState.status === 'FAILED' && isOwner && onRetryCopy;
  if (!deleteAction) {
    if (showRetry) return <BotCopyRetryAction onRetry={() => onRetryCopy(bot)} />;
    return null;
  }
  return (
    <div className="flex items-center justify-end gap-0" onClick={(event) => event.stopPropagation()}>
      {showRetry ? <BotCopyRetryAction onRetry={() => onRetryCopy(bot)} /> : null}
      <BotCopyDeleteAction bot={bot} onAction={deleteAction} />
    </div>
  );
}
