import { getCapabilities } from '@/capabilities';
import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { ChevronDown, Info, Plus, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { BotRegistrationDialog } from './BotRegistrationDialog';
import { IdentityAvatar, IdentityDetails, IdentitySection } from './IdentitySelectorParts';

interface WorkspaceIdentitySelectorProps {
  identities: Identity[];
  activeId: string | null;
  onChange: (id: string) => void;
  onOpenPermissions?: () => void;
  /** 顶栏右侧当前用户头像；所有用户身份复用该头像，Bot 仍使用自身头像。 */
  userAvatarUrl?: string;
  layout?: 'default' | 'sidebar';
}

/** Workspace 业务层身份选择器：只消费已映射的 Identity，不直接读取 Store 或调用接口。 */
export function WorkspaceIdentitySelector({
  identities,
  activeId,
  onChange,
  onOpenPermissions,
  userAvatarUrl,
  layout = 'default',
}: WorkspaceIdentitySelectorProps) {
  const [open, setOpen] = useState(false);
  const activeIdentity = identities.find((identity) => identity.id === activeId) ?? identities[0] ?? null;
  const userIdentities = identities.filter((identity) => identity.kind === 'user');
  const botIdentities = identities.filter((identity) => identity.kind === 'bot');
  const sidebarLayout = layout === 'sidebar';
  const botRegistrationEnabled = getCapabilities().getBotRegistrationEnabled().value;
  const [botRegistrationOpen, setBotRegistrationOpen] = useState(false);

  return (
    <div className="space-y-1">
      {sidebarLayout ? (
        <TooltipProvider delayDuration={300}>
          <div className="flex items-center gap-1 px-1 pb-1 text-xs font-semibold text-foreground">
            <span>工作身份</span>
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  role="img"
                  aria-label="工作身份说明"
                  tabIndex={0}
                  className="relative top-px inline-flex cursor-help items-center text-muted-foreground/70"
                >
                  <Info className="h-3 w-3" aria-hidden />
                </span>
              </TooltipTrigger>
              <TooltipContent>当前协作身份决定在对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围</TooltipContent>
            </Tooltip>
          </div>
        </TooltipProvider>
      ) : (
        <TooltipProvider delayDuration={300}>
          <div className="flex items-center gap-1 px-1 text-xs font-medium text-foreground">
            <span>当前协作身份</span>
            {identities.length > 1 ? (
              <span className="text-[10px] font-normal text-muted-foreground">可切换其他协作身份</span>
            ) : null}
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  role="img"
                  aria-label="协作身份说明"
                  tabIndex={0}
                  className="inline-flex cursor-help items-center text-muted-foreground"
                >
                  <Info className="h-3.5 w-3.5" aria-hidden />
                </span>
              </TooltipTrigger>
              <TooltipContent>当前协作身份决定在对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围</TooltipContent>
            </Tooltip>
          </div>
        </TooltipProvider>
      )}
      {activeIdentity ? (
        <>
          <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
              <Button
                variant={sidebarLayout ? 'ghost' : 'outline'}
                aria-expanded={open}
                aria-label={`当前协作身份：${activeIdentity.name}`}
                className={cn(
                  'h-auto w-full justify-between gap-2 text-left',
                  sidebarLayout
                    ? 'min-h-9 rounded-lg border border-border bg-muted/60 px-2.5 py-1.5 text-foreground hover:bg-muted hover:text-foreground'
                    : 'min-h-10 rounded-lg px-2 py-1',
                  sidebarLayout && open && 'border-primary',
                )}
              >
                <IdentityAvatar
                  identity={activeIdentity}
                  size={sidebarLayout ? 'xs' : 'sm'}
                  userAvatarUrl={userAvatarUrl}
                />
                <IdentityDetails identity={activeIdentity} compact={sidebarLayout} summaryOnly={sidebarLayout} />
                <ChevronDown
                  className={cn(
                    sidebarLayout ? 'h-3.5 w-3.5' : 'h-4 w-4',
                    'shrink-0 text-muted-foreground transition-transform',
                    open && 'rotate-180',
                  )}
                />
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              className="w-[320px] max-w-[calc(100vw-24px)] p-2"
              onOpenAutoFocus={(event) => {
                // 不将焦点自动落到说明图标，避免打开身份列表时立即触发 Tooltip。
                event.preventDefault();
              }}
            >
              <div className="mb-2 flex items-center justify-between gap-2 border-b border-border px-2.5 pb-2">
                <div className="flex min-w-0 items-center gap-1">
                  <p className="text-xs font-medium text-foreground">切换协作身份</p>
                  {!sidebarLayout ? (
                    <TooltipProvider delayDuration={300}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            role="img"
                            aria-label="协作身份说明"
                            tabIndex={0}
                            className="inline-flex cursor-help items-center text-muted-foreground"
                          >
                            <Info className="h-3.5 w-3.5" aria-hidden />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          当前协作身份决定在对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : null}
                </div>
                {onOpenPermissions ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label="进入协作权限设置"
                    className="h-7 shrink-0 gap-1 px-1.5 text-[10px] text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      setOpen(false);
                      onOpenPermissions();
                    }}
                  >
                    <ShieldCheck className="h-3.5 w-3.5 text-primary" aria-hidden />
                    协作权限
                  </Button>
                ) : null}
              </div>
              <div className="app-scrollbar max-h-80 space-y-3 overflow-y-auto">
                <IdentitySection
                  title="用户身份"
                  identities={userIdentities}
                  activeId={activeId}
                  onSelect={(id) => {
                    onChange(id);
                    setOpen(false);
                  }}
                  userAvatarUrl={userAvatarUrl}
                />
                <IdentitySection
                  title="Bot 身份"
                  identities={botIdentities}
                  activeId={activeId}
                  onSelect={(id) => {
                    onChange(id);
                    setOpen(false);
                  }}
                  userAvatarUrl={userAvatarUrl}
                />
                {identities.length === 0 ? (
                  <p className="px-2.5 py-4 text-center text-xs text-muted-foreground">暂无可协作身份</p>
                ) : null}
              </div>
              {botRegistrationEnabled ? (
                <div className="mt-2 border-t border-border pt-2">
                  <Button
                    variant="ghost"
                    className="h-auto w-full justify-start gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-primary hover:bg-accent hover:text-primary"
                    onClick={() => {
                      setOpen(false);
                      setBotRegistrationOpen(true);
                    }}
                  >
                    <Plus aria-hidden className="h-3.5 w-3.5" />
                    接入新的 Bot
                  </Button>
                </div>
              ) : null}
            </PopoverContent>
          </Popover>
          {botRegistrationEnabled ? (
            <BotRegistrationDialog open={botRegistrationOpen} onClose={() => setBotRegistrationOpen(false)} />
          ) : null}
        </>
      ) : (
        <div
          className={cn(
            'px-3 py-3 text-center text-xs text-muted-foreground',
            !sidebarLayout && 'rounded-lg border border-dashed border-border',
          )}
        >
          暂无可协作身份
        </div>
      )}
    </div>
  );
}
