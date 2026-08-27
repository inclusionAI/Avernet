import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { BotDomain } from '@/services/botWorkshop';
import { ArrowUpRight, MoreHorizontal, Users } from 'lucide-react';
import { useState } from 'react';
import { actionIcon, actionLabel, type BotCardManagementAction } from './config';

interface BotManagementMenuProps {
  bot: BotDomain;
  collaborationMode: 'authorize' | 'request';
  lockedByOther: boolean;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onManagePublication?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
}

export function BotManagementMenu(props: BotManagementMenuProps) {
  const { bot, collaborationMode, lockedByOther, onAction, onManagePublication, onChangeSpace, onAuthorize } = props;
  const [open, setOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
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
              onClick={() => onManagePublication(bot)}
            >
              发布与阶段推进
            </Button>
          ) : null}
          {bot.serviceMode === 'non-service' && bot.deployment === 'cloud' ? (
            <ConfirmDialog
              title="开启服务化"
              description="开启后不可逆，确认将此 Bot 转换为服务 Bot？"
              onConfirm={() => onAction('upgrade', bot)}
              disabled={!['openclaw', 'teclaw'].includes(bot.runtime.engine)}
            >
              <Button variant="ghost" size="sm" className="w-full justify-start" leftIcon={actionIcon.upgrade}>
                {actionLabel.upgrade}
              </Button>
            </ConfirmDialog>
          ) : null}
          <ConfirmDialog
            title="重启 Bot"
            description="将重新拉起整个 Bot 容器，现有会话可能中断。"
            onConfirm={() => onAction('restart', bot)}
            disabled={lockedByOther || !bot.actions.includes('restart')}
          >
            <Button variant="ghost" size="sm" className="w-full justify-start" leftIcon={actionIcon.restart}>
              {actionLabel.restart}
            </Button>
          </ConfirmDialog>
          <ConfirmDialog
            title="重启引擎"
            description="仅重启引擎进程，不重建容器。"
            onConfirm={() => onAction('engine_restart', bot)}
            disabled={lockedByOther || !bot.actions.includes('engine_restart')}
          >
            <Button variant="ghost" size="sm" className="w-full justify-start" leftIcon={actionIcon.engine_restart}>
              {actionLabel.engine_restart}
            </Button>
          </ConfirmDialog>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            disabled={lockedByOther}
            onClick={() => onChangeSpace?.(bot)}
          >
            变更归属空间
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<Users className="size-4" />}
            onClick={() => onAuthorize?.(bot)}
          >
            {collaborationMode === 'authorize' ? '授权' : '申请操作权限'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-[var(--color-danger)]"
            leftIcon={actionIcon.delete}
            disabled={lockedByOther || !bot.actions.includes('delete')}
            onClick={() => {
              setOpen(false);
              setDeleteOpen(true);
            }}
          >
            {actionLabel.delete}
          </Button>
        </PopoverContent>
      </Popover>
      <ConfirmDialog
        open={deleteOpen}
        title="确认删除 Bot"
        description={`删除「${bot.name}」后无法恢复。`}
        confirmText="删除"
        confirmVariant="destructive"
        onCancel={() => setDeleteOpen(false)}
        onConfirm={async () => {
          await onAction('delete', bot);
          setDeleteOpen(false);
        }}
      />
    </>
  );
}
