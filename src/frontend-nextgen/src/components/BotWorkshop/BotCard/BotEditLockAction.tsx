import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import { Lock } from 'lucide-react';

export function BotEditLockAction({
  bot,
  onClaimLock,
}: {
  bot: BotDomain;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
}) {
  const lock = bot.lock;
  if (bot.serviceMode !== 'service' || bot.lifecycle !== 'draft' || !lock) return null;
  const timeText = lock.lockedAt ? `（锁定时间：${lock.lockedAt}）` : '';
  if (lock.status === 'mine') {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex text-[var(--color-primary)]" aria-label={`你正在编辑 ${bot.name}`}>
              <Lock aria-hidden className="size-3.5" />
            </span>
          </TooltipTrigger>
          <TooltipContent>你正在编辑{timeText}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
  const holder = lock.holderName || lock.holderUserId || '其他协作者';
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <ConfirmDialog
              title="该 Bot 正在被编辑"
              description={
                <span className="space-y-2">
                  <span className="block">
                    当前「{bot.name}」正由 {holder} 编辑中。
                  </span>
                  {lock.lockedAt ? (
                    <span className="block text-[var(--color-muted)]">锁定时间：{lock.lockedAt}</span>
                  ) : null}
                  <span className="block">抢占后对方将无法继续保存当前编辑内容，确认要抢锁并进入编辑吗？</span>
                </span>
              }
              confirmText="抢锁并编辑"
              cancelText="先不抢锁"
              confirmVariant="destructive"
              onConfirm={() => onClaimLock?.(bot)}
              disabled={!onClaimLock}
            >
              <Button
                variant="ghost"
                size="icon"
                className="size-6 text-[var(--color-danger)]"
                aria-label={`抢占 ${bot.name} 的编辑锁`}
                leftIcon={<Lock className="size-3.5" />}
              />
            </ConfirmDialog>
          </span>
        </TooltipTrigger>
        <TooltipContent>
          正在由 {holder} 编辑中{timeText}，点击可抢占
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
