// 经典（非 inline）身份弹层内容：标题区 + 计数胶囊 + 用户/Bot 分组。
// 从 WorkspaceIdentitySelector/index.tsx 拆出（工程红线：Component 单文件 ≤300 行），
// 渲染语义与拆分前一一对应，无行为变化。
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { Identity } from '@/services/workspace/workspaceModel';
import { Bot, Info, UserRound, Users } from 'lucide-react';
import { IdentitySection } from './IdentitySelectorParts';

export function ClassicIdentityPopoverBody({
  identities,
  userIdentities,
  botIdentities,
  activeId,
  onSelect,
  userAvatarUrl,
  popoverTitle,
  popoverHint,
  showInfoIcon,
  emptyIdentityLabel,
}: {
  identities: Identity[];
  userIdentities: Identity[];
  botIdentities: Identity[];
  activeId: string | null;
  onSelect: (id: string) => void;
  userAvatarUrl?: string;
  popoverTitle?: string;
  popoverHint?: string;
  showInfoIcon: boolean;
  emptyIdentityLabel: string;
}) {
  return (
    <>
      <div className="border-b border-divider/70 px-1.5 pb-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1">
            <p className="m-0 text-xs font-semibold text-foreground">{popoverTitle ?? '切换工作身份'}</p>
            {showInfoIcon ? (
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
                    当前工作身份决定你以个人或指定 Bot
                    身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : null}
          </div>
          {/* 右端计数胶囊：标题行第二信息点，直接回答「我有几个身份」。 */}
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">
            {identities.length} 个身份
          </span>
        </div>
        <p className="m-0 mt-1 truncate text-[11px] text-muted-foreground">{popoverHint ?? '切换后对工作区全局生效'}</p>
      </div>
      <div className="app-scrollbar max-h-80 space-y-1 overflow-y-auto px-0.5">
        <IdentitySection
          title="用户身份"
          icon={UserRound}
          identities={userIdentities}
          activeId={activeId}
          onSelect={onSelect}
          userAvatarUrl={userAvatarUrl}
        />
        <IdentitySection
          title="Bot 身份"
          icon={Bot}
          identities={botIdentities}
          activeId={activeId}
          onSelect={onSelect}
          userAvatarUrl={userAvatarUrl}
        />
        {identities.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-2 py-5 text-center">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <Users className="h-4 w-4" aria-hidden />
            </span>
            <p className="m-0 text-xs text-muted-foreground">{emptyIdentityLabel}</p>
          </div>
        ) : null}
      </div>
    </>
  );
}
