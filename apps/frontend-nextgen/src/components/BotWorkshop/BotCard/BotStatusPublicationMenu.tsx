import { Button } from '@/components/ui/Button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { BotDomain } from '@/services/botWorkshop';
import { ArrowUpRight, ChevronDown } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { publicationManagementAvailability } from './botManagementPolicy';
import { ActionHelp } from './menuPrimitives';

interface Props {
  bot: BotDomain;
  children: ReactNode;
  onManagePublication?: (bot: BotDomain) => void;
}

/** 状态列的发布入口；复用原菜单的可用性判断与发布回调。 */
export function BotStatusPublicationMenu({ bot, children, onManagePublication }: Props) {
  const [open, setOpen] = useState(false);
  const publication = publicationManagementAvailability(bot);
  if (bot.serviceMode !== 'service' || !publication.visible || !onManagePublication) return <>{children}</>;

  const lockedByOther = bot.lock?.status === 'other';
  const disabledReason = lockedByOther ? '该 Bot 正被他人编辑，请先抢锁' : publication.disabledReason;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`${bot.name} 发布状态`}
          className="-ml-2 h-7 gap-1 rounded-md px-2 hover:bg-muted data-[state=open]:bg-muted"
          onClick={(event) => event.stopPropagation()}
        >
          {children}
          <ChevronDown className={`size-3.5 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-52 p-1" onClick={(event) => event.stopPropagation()}>
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start"
          leftIcon={<ArrowUpRight className="size-4" />}
          disabled={lockedByOther || !publication.enabled}
          onClick={() => {
            setOpen(false);
            onManagePublication(bot);
          }}
        >
          {disabledReason ? (
            <ActionHelp description={disabledReason}>发布与阶段推进</ActionHelp>
          ) : (
            '发布与阶段推进'
          )}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
