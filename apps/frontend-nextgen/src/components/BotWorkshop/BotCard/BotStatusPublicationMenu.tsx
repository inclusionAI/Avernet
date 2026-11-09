import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { ServicePublication } from '@/domain/botEditor';
import {
  isPublicationMenuAction,
  publicationActionLabel,
  publicationStatusLabel,
  useBotPublicationMenu,
  type PublicationMenuAction,
} from '@/hooks/useBotPublicationMenu';
import type { BotDomain } from '@/services/botWorkshop';
import { ChevronDown, ExternalLink } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { publicationManagementAvailability } from './botManagementPolicy';

interface Props {
  bot: BotDomain;
  children: ReactNode;
  onPublicationChanged?: () => Promise<void>;
  onPublicationGateBlocked?: (bot: BotDomain) => void;
}

/** 状态下拉直接提供当前版本可执行的操作，确认后调用原生命周期接口。 */
export function BotStatusPublicationMenu({
  bot,
  children,
  onPublicationChanged,
  onPublicationGateBlocked,
}: Props) {
  const [open, setOpen] = useState(false);
  const [selection, setSelection] = useState<{ item: ServicePublication; action: PublicationMenuAction }>();
  const closeTimer = useRef<ReturnType<typeof setTimeout>>();
  const openedByHover = useRef(false);
  const cancelClose = () => clearTimeout(closeTimer.current);
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setOpen(false), 150);
  };
  useEffect(() => () => clearTimeout(closeTimer.current), []);
  const publication = publicationManagementAvailability(bot);
  const enabled = bot.serviceMode === 'service' && publication.visible && Boolean(onPublicationChanged);
  const menu = useBotPublicationMenu(bot, enabled && open, onPublicationChanged);
  if (!enabled) return <>{children}</>;

  const disabledReason =
    bot.lock?.status === 'other' ? '该 Bot 正被他人编辑，暂时无法执行发布操作。' : publication.disabledReason;
  const actionable = menu.items.filter(
    (item) => item.availableActions.some(isPublicationMenuAction) || item.approval?.approvalUrl,
  );
  return (
    <div className="inline-flex" onClick={(event) => event.stopPropagation()}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`${bot.name} 发布状态`}
            className="-ml-2 h-7 gap-1 rounded-md px-2 hover:bg-muted data-[state=open]:bg-muted"
            onMouseEnter={() => {
              cancelClose();
              openedByHover.current = true;
              setOpen(true);
            }}
            onMouseLeave={scheduleClose}
            onClick={(event) => {
              event.stopPropagation();
              cancelClose();
              openedByHover.current = false;
            }}
          >
            {children}
            <ChevronDown
              className={`size-3.5 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`}
            />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="w-60 p-1"
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          onOpenAutoFocus={(event) => {
            if (openedByHover.current) event.preventDefault();
          }}
          onCloseAutoFocus={(event) => {
            if (openedByHover.current) event.preventDefault();
          }}
          onClick={(event) => event.stopPropagation()}
        >
          {disabledReason ? (
            <p className="m-0 px-2 py-2 text-xs text-muted-foreground">{disabledReason}</p>
          ) : menu.loading ? (
            <p role="status" className="m-0 px-2 py-2 text-xs text-muted-foreground">
              正在加载可用操作…
            </p>
          ) : menu.error ? (
            <div className="p-2 text-xs text-muted-foreground">
              发布状态加载失败
              <Button size="sm" variant="ghost" onClick={() => void menu.reload()}>
                重试
              </Button>
            </div>
          ) : !actionable.length ? (
            <p className="m-0 px-2 py-2 text-xs text-muted-foreground">当前暂无可执行操作</p>
          ) : (
            actionable.map((item) => (
              <div key={item.cardId} className="py-1">
                {item.deployment?.errorMessage ? (
                  <p className="m-0 px-2 py-1 text-xs text-destructive">{item.deployment.errorMessage}</p>
                ) : null}
                {item.approval?.required ? (
                  <p className="m-0 px-2 py-1 text-xs text-muted-foreground">
                    审批状态：{item.approval.status || '等待提交'}
                  </p>
                ) : null}
                {item.availableActions.filter(isPublicationMenuAction).map((action) => (
                  <Button
                    key={action}
                    variant="ghost"
                    size="sm"
                    disabled={menu.pending}
                    className={`w-full justify-start ${
                      action === 'offline' || action === 'cancel_staging'
                        ? 'text-destructive hover:text-destructive'
                        : ''
                    }`}
                    onClick={() => {
                      cancelClose();
                      setSelection({ item, action });
                      setOpen(false);
                    }}
                  >
                    {publicationActionLabel[action]}
                  </Button>
                ))}
                {item.approval?.approvalUrl ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start"
                    rightIcon={<ExternalLink className="size-3" />}
                    onClick={() => window.open(item.approval!.approvalUrl, '_blank', 'noopener,noreferrer')}
                  >
                    查看审批
                  </Button>
                ) : null}
              </div>
            ))
          )}
        </PopoverContent>
      </Popover>
      <ConfirmDialog
        open={Boolean(selection)}
        loading={menu.pending}
        title={selection ? publicationActionLabel[selection.action] : '确认操作'}
        description={
          selection
            ? `确认对「${bot.name}」V${selection.item.version}（${
                publicationStatusLabel[selection.item.status]
              }）执行“${publicationActionLabel[selection.action]}”？${
                selection.action === 'offline'
                  ? '下线后该服务将不可用。'
                  : selection.action === 'upgrade'
                  ? '将基于此版本创建新的升级草稿。'
                  : ''
              }`
            : undefined
        }
        confirmText={selection ? publicationActionLabel[selection.action] : '确定'}
        confirmVariant={
          selection?.action === 'offline' || selection?.action === 'cancel_staging' ? 'destructive' : 'primary'
        }
        onCancel={() => setSelection(undefined)}
        onConfirm={async () => {
          const result = selection ? await menu.run(selection.action, selection.item) : 'failed';
          if (result === 'success' || result === 'blocked') setSelection(undefined);
          if (result === 'blocked') onPublicationGateBlocked?.(bot);
        }}
      />
    </div>
  );
}
