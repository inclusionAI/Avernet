import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import { Lock, LockOpen } from 'lucide-react';

export function BotEditLockAction({
  bot,
  onClaimLock,
  onReleaseLock,
}: {
  bot: BotDomain;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  onReleaseLock?: (bot: BotDomain) => Promise<void>;
}) {
  const lock = bot.lock;
  if (bot.serviceMode !== 'service' || bot.lifecycle !== 'draft' || (!lock && !bot.needsEditLock)) return null;
  const mine = lock?.status === 'mine';
  const other = lock?.status === 'other';
  const holder = lock?.holderName || lock?.holderUserId || '其他协作者';
  const timeText = lock?.lockedAt ? `（锁定时间：${lock.lockedAt}）` : '';
  const label = mine ? '释放锁' : other ? '抢锁' : '获取锁';
  const tooltip = mine
    ? `你正在编辑${timeText}，点击可释放`
    : other
    ? `正在由 ${holder} 编辑中${timeText}，点击可抢占`
    : '当前无人持锁，获取后可编辑';
  return (
    <span onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <ConfirmDialog
                title={mine ? '释放编辑锁' : other ? '该 Bot 正在被编辑' : '获取编辑锁'}
                description={
                  <span className="space-y-2">
                    <span className="block">
                      {mine
                        ? `释放「${bot.name}」的编辑锁后，其他协作者可以获取锁并编辑。`
                        : other
                        ? `当前「${bot.name}」正由 ${holder} 编辑中。`
                        : `确认获取「${bot.name}」的编辑锁并进入编辑？`}
                    </span>
                    {lock?.lockedAt ? (
                      <span className="block text-muted-foreground">锁定时间：{lock.lockedAt}</span>
                    ) : null}
                    {other ? (
                      <span className="block">抢占后对方将无法继续保存当前编辑内容，确认要抢锁并进入编辑吗？</span>
                    ) : null}
                    {mine ? (
                      <span className="block">请先保存其他标签页中尚未提交的修改，释放后继续保存可能失败。</span>
                    ) : null}
                  </span>
                }
                confirmText={mine ? '确认释放' : other ? '抢锁并编辑' : '获取锁并编辑'}
                cancelText={other ? '先不抢锁' : '取消'}
                confirmVariant={other ? 'destructive' : 'primary'}
                onConfirm={() => (mine ? onReleaseLock?.(bot) : onClaimLock?.(bot))}
                disabled={mine ? !onReleaseLock : !onClaimLock}
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className={`h-6 px-1 text-xs ${other ? 'text-destructive' : 'text-primary'}`}
                  disabled={mine ? !onReleaseLock : !onClaimLock}
                  aria-label={other ? `抢占 ${bot.name} 的编辑锁` : `${label}：${bot.name}`}
                  leftIcon={lock ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
                >
                  {label}
                </Button>
              </ConfirmDialog>
            </span>
          </TooltipTrigger>
          <TooltipContent>{tooltip}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </span>
  );
}
