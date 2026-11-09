/**
 * BotWorkshop「公开设置」弹窗入口：以 `[Backend bot.id, 认证 Human id]` 组合 key 挂载有状态主体。
 * 切换 Bot/身份时旧主体整体卸载，修复报告与隐私读取在途结果不会残留到新目标（AC-29/34）。
 * 标题、组件名与对外 API 保持不变；具体分区装配见同目录 BotGeneralConfigDialogBody。
 */
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import {
  BotGeneralConfigDialogBody,
  type BotGeneralConfigDialogProps,
} from './BotGeneralConfigDialogBody';

export type { BotGeneralConfigDialogProps };

export function BotGeneralConfigDialog({ bot, isExternalBot = false, onClose }: BotGeneralConfigDialogProps) {
  const human = useHumanIdentity();
  const userId = human.identity?.userId ?? '';
  return (
    <BotGeneralConfigDialogBody
      key={`${bot.id}:${userId}`}
      bot={bot}
      isExternalBot={isExternalBot}
      onClose={onClose}
      human={human}
    />
  );
}
