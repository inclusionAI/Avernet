import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { BotDomain } from '@/domain/botWorkshop';
import { restartPublishStageLabel, restartPublishStageOf } from '@/domain/botWorkshop';
import { useState } from 'react';
import { actionLabel, type BotCardManagementAction } from './config';

interface BotManagementConfirmDialogProps {
  bot: BotDomain;
  action?: BotCardManagementAction;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onCancel: () => void;
}

export function BotManagementConfirmDialog({ bot, action, onAction, onCancel }: BotManagementConfirmDialogProps) {
  const [loading, setLoading] = useState(false);
  const restartPublishStage = action === 'restart_publish' ? restartPublishStageOf(bot.lifecycle) : undefined;
  return (
    <ConfirmDialog
      open={Boolean(action)}
      loading={loading}
      title={action === 'delete' ? '确认删除 Bot' : action ? actionLabel[action] : '确认'}
      description={
        action === 'delete'
          ? `删除「${bot.name}」后无法恢复。`
          : action === 'upgrade'
          ? '开启后不可逆，确认将此 Bot 转换为服务 Bot？'
          : action === 'engine_restart'
          ? '仅重启引擎进程，不重建容器。'
          : action === 'restart_publish'
          ? restartPublishStage
            ? `将重启该服务在${restartPublishStageLabel[restartPublishStage]}环境发布的运行时，进行中的服务会话可能短暂中断；草稿机器与草稿数据不受影响。`
            : '当前发布状态不支持重启发布，请刷新 Bot 列表后重试。'
          : '将重新拉起整个 Bot 容器，现有会话可能中断。'
      }
      confirmText={action === 'delete' ? '删除' : '确认'}
      confirmVariant={action === 'delete' ? 'destructive' : 'primary'}
      onCancel={onCancel}
      onConfirm={async () => {
        if (!action) return;
        setLoading(true);
        try {
          await onAction(action, bot);
          onCancel();
        } catch {
          // Failure feedback is handled by the action service; keep the dialog open for retry.
        } finally {
          setLoading(false);
        }
      }}
    />
  );
}
