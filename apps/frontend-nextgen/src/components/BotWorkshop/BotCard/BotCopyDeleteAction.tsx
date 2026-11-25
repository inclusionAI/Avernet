import { Button } from '@/components/ui/Button';
import type { BotDomain } from '@/domain/botWorkshop';
import { Trash2 } from 'lucide-react';
import { useState } from 'react';
import { BotManagementConfirmDialog } from './BotManagementConfirmDialog';
import type { BotCardManagementAction } from './config';

/**
 * 复制进行中的行内删除入口。copy_state 非空时操作列收敛为
 * 重试(FAILED)+删除 —— view/edit/chat/logs/三点菜单一律隐藏,删除走与
 * 三点菜单内删除项**完全相同**的 ``onAction('delete', bot)`` + 原确认弹窗
 * (``BotManagementConfirmDialog``),page 层 handler 在删除成功后自带
 * ``load()`` 刷新列表。开关由调用方 ``BOT_COPY_FEATURE_ENABLED`` 把守,
 * 组件本身不读开关。
 */
function BotCopyDeleteAction({
  bot,
  onAction,
}: {
  bot: BotDomain;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
}) {
  const [confirm, setConfirm] = useState(false);
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="gap-1 text-destructive hover:bg-destructive/10"
        onClick={(event) => {
          event.stopPropagation();
          setConfirm(true);
        }}
      >
        <Trash2 aria-hidden className="size-3.5" />
        删除
      </Button>
      <BotManagementConfirmDialog
        bot={bot}
        action={confirm ? 'delete' : undefined}
        onAction={onAction}
        onCancel={() => setConfirm(false)}
      />
    </>
  );
}

export default BotCopyDeleteAction;
