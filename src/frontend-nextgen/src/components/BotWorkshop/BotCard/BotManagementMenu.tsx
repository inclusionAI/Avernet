import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { BotDomain } from '@/services/botWorkshop';
import { ArrowUpRight, MapPin, MoreHorizontal, Users } from 'lucide-react';
import { useState } from 'react';
import { actionIcon, actionLabel, type BotCardManagementAction } from './config';

interface BotManagementMenuProps {
  bot: BotDomain;
  collaborationMode?: 'authorize' | 'request';
  lockedByOther: boolean;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onManagePublication?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
}

export function BotManagementMenu(props: BotManagementMenuProps) {
  const { bot, collaborationMode, lockedByOther, onAction, onManagePublication, onChangeSpace, onAuthorize } = props;
  const [open, setOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<'upgrade' | 'restart' | 'engine_restart' | 'delete'>();
  const [confirming, setConfirming] = useState(false);
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`管理 ${bot.name}`}
            leftIcon={<MoreHorizontal className="size-4" />}
          />
        </PopoverTrigger>
        <PopoverContent align="end" className="w-52 space-y-1 p-2">
          {bot.serviceMode === 'service' && onManagePublication ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={<ArrowUpRight className="size-4" />}
              disabled={lockedByOther}
              onClick={() => {
                setOpen(false);
                onManagePublication(bot);
              }}
            >
              发布与阶段推进
            </Button>
          ) : null}
          {bot.serviceMode === 'non-service' && bot.deployment === 'cloud' && bot.canUpgradeToService ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={actionIcon.upgrade}
              disabled={lockedByOther}
              onClick={() => {
                setOpen(false);
                setConfirmAction('upgrade');
              }}
            >
              {actionLabel.upgrade}
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={actionIcon.restart}
            disabled={lockedByOther || !bot.actions.includes('restart')}
            onClick={() => {
              setOpen(false);
              setConfirmAction('restart');
            }}
          >
            {actionLabel.restart}
          </Button>
          {!isAgentCodingBot ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={actionIcon.engine_restart}
              disabled={lockedByOther || !bot.actions.includes('engine_restart')}
              onClick={() => {
                setOpen(false);
                setConfirmAction('engine_restart');
              }}
            >
              {actionLabel.engine_restart}
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<MapPin className="size-4" />}
            disabled={lockedByOther}
            onClick={() => {
              setOpen(false);
              onChangeSpace?.(bot);
            }}
          >
            变更归属空间
          </Button>
          {!isAgentCodingBot && collaborationMode && onAuthorize ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={<Users className="size-4" />}
              onClick={() => {
                setOpen(false);
                onAuthorize(bot);
              }}
            >
              {collaborationMode === 'authorize' ? '授权' : '申请操作权限'}
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-destructive"
            leftIcon={actionIcon.delete}
            disabled={lockedByOther || !bot.actions.includes('delete')}
            onClick={() => {
              setOpen(false);
              setConfirmAction('delete');
            }}
          >
            {actionLabel.delete}
          </Button>
        </PopoverContent>
      </Popover>
      <ConfirmDialog
        open={Boolean(confirmAction)}
        loading={confirming}
        title={
          confirmAction === 'delete'
            ? '确认删除 Bot'
            : confirmAction === 'upgrade'
            ? '开启服务化'
            : confirmAction === 'engine_restart'
            ? '重启引擎'
            : '重启 Bot'
        }
        description={
          confirmAction === 'delete'
            ? `删除「${bot.name}」后无法恢复。`
            : confirmAction === 'upgrade'
            ? '开启后不可逆，确认将此 Bot 转换为服务 Bot？'
            : confirmAction === 'engine_restart'
            ? '仅重启引擎进程，不重建容器。'
            : '将重新拉起整个 Bot 容器，现有会话可能中断。'
        }
        confirmText={confirmAction === 'delete' ? '删除' : '确认'}
        confirmVariant={confirmAction === 'delete' ? 'destructive' : 'primary'}
        onCancel={() => setConfirmAction(undefined)}
        onConfirm={async () => {
          if (!confirmAction) return;
          setConfirming(true);
          try {
            await onAction(confirmAction, bot);
            setConfirmAction(undefined);
          } finally {
            setConfirming(false);
          }
        }}
      />
    </>
  );
}
