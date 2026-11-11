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
 */
export function BotCopyActionsCell({
  bot,
  copyState,
  onRetryCopy,
  onAction,
}: {
  bot: BotDomain;
  copyState: BotCopyStateSummary;
  onRetryCopy?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
}) {
  // dst 已 ACTIVE 才能删除(中间态 dst 尚未起,删无意义);FAILED 必然 ACTIVE。
  const deleteAction = bot.lifecycle === 'running' ? onAction : undefined;
  if (!deleteAction) {
    if (copyState.status === 'FAILED' && onRetryCopy)
      return <BotCopyRetryAction onRetry={() => onRetryCopy(bot)} />;
    return null;
  }
  return (
    <div className="flex items-center justify-end gap-0" onClick={(event) => event.stopPropagation()}>
      {copyState.status === 'FAILED' && onRetryCopy ? (
        <BotCopyRetryAction onRetry={() => onRetryCopy(bot)} />
      ) : null}
      <BotCopyDeleteAction bot={bot} onAction={deleteAction} />
    </div>
  );
}
