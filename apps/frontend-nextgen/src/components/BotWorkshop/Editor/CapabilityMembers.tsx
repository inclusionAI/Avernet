import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotEditorCli, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { cn } from '@/utils/cn';
import { Eye, FileCode2, FileText, Loader2, Plus, SquareTerminal, Trash2, UserRound } from 'lucide-react';
import { useState } from 'react';

interface CapabilityMembersProps {
  kind: 'skill' | 'mcp' | 'cli';
  items: Array<BotEditorSkill | BotEditorMcp | BotEditorCli>;
  /** 调试面板同屏时避免横向挤压，卡片改为纵向单列。 */
  compact?: boolean;
  editable: boolean;
  identities?: Record<string, 'caller' | 'owner'>;
  identityEditable?: boolean;
  identityDisabledReason?: string;
  updatingIdentityId?: string;
  onIdentity?: (id: string, identity: 'caller' | 'owner') => Promise<void>;
  onAdd?: () => void;
  onRemove?: (id: string) => Promise<void>;
  onDetail?: (id: string, name: string) => void;
}

function sourceBadge(item: BotEditorSkill | BotEditorMcp | BotEditorCli) {
  if (!('source' in item) || !item.source) return null;
  const source = item.source;
  if (source === 'teamclaw-market' || source === 'skillcenter-market' || source === 'market') {
    return <Badge tone="success" className="rounded-sm px-1 py-0 text-[10px] leading-4">市场</Badge>;
  }
  if (source === 'workshop') {
    return <Badge tone="primary" className="rounded-sm px-1 py-0 text-[10px] leading-4">空间</Badge>;
  }
  return <Badge tone="primary" className="rounded-sm px-1 py-0 text-[10px] leading-4">本地</Badge>;
}

export function CapabilityMembers({
  kind,
  items,
  compact = false,
  editable,
  identities = {},
  onIdentity,
  identityEditable = false,
  identityDisabledReason,
  updatingIdentityId,
  onAdd,
  onRemove,
  onDetail,
}: CapabilityMembersProps) {
  const [removingId, setRemovingId] = useState<string>();
  return (
    <div>
      <div className="mb-2 flex h-5 items-center justify-between">
        <span className="text-sm font-medium text-foreground">
          {kind === 'skill' ? 'Skills' : kind === 'mcp' ? 'MCPs' : 'CLIs'}
        </span>
        {kind !== 'cli' && onAdd ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-0 text-muted-foreground hover:bg-transparent"
            disabled={!editable}
            leftIcon={<Plus className="size-3" />}
            onClick={onAdd}
          >
            添加
          </Button>
        ) : null}
      </div>
      {items.length ? (
        <div className={cn('grid grid-cols-1 gap-2', !compact && 'md:grid-cols-2 xl:grid-cols-3')}>
          {items.map((item) => {
          const id = 'serverCode' in item ? item.serverCode : 'code' in item ? item.code : item.id;
          const identity = identities[id] ?? 'owner';
          return (
            <div
              key={id}
              className="group flex h-10 min-w-0 items-center gap-2 rounded-lg border border-border bg-card px-3 shadow-sm transition-colors hover:bg-muted/40"
            >
              {kind === 'skill' ? (
                <FileText className="size-4 shrink-0 text-success" />
              ) : kind === 'mcp' ? (
                <FileCode2 className="size-4 shrink-0 text-purple" />
              ) : (
                <SquareTerminal className="size-4 shrink-0 text-primary" />
              )}
              <span className="min-w-0 flex-1 truncate text-sm text-foreground">{item.name}</span>
              {'version' in item && item.version ? <Badge tone="outline" className="rounded-sm px-1 py-0 text-[10px] leading-4">{item.version}</Badge> : null}
              {sourceBadge(item)}
              {kind === 'skill' && onDetail ? (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5 shrink-0 p-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                  aria-label={`查看${item.name}详情`}
                  leftIcon={<Eye className="size-3.5" />}
                  onClick={() => onDetail(id, item.name)}
                />
              ) : null}
              {kind === 'mcp' && identityEditable ? (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      disabled={!editable || !identityEditable || updatingIdentityId === id}
                      aria-label={`修改${item.name}调用身份`}
                      size="icon"
                      className="size-5 shrink-0 p-0 text-muted-foreground hover:bg-transparent"
                      leftIcon={
                        updatingIdentityId === id ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <UserRound className="size-3.5" />
                        )
                      }
                    />
                  </PopoverTrigger>
                  <PopoverContent align="end" className="w-72 space-y-2 p-2">
                    <p className="m-0 px-2 py-1 text-xs text-muted-foreground">
                      选择该 MCP 执行时使用的身份。修改仅作用于当前草稿。
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto w-full items-start justify-start py-2 text-left"
                      leftIcon={<UserRound className="mt-0.5 size-3.5" />}
                      disabled={identity === 'owner'}
                      onClick={() => void onIdentity?.(id, 'owner')}
                    >
                      <span>
                        <span className="block">Owner 模式</span>
                        <span className="block font-normal text-muted-foreground">使用 Bot 所有者身份调用</span>
                      </span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto w-full items-start justify-start py-2 text-left"
                      leftIcon={<UserRound className="mt-0.5 size-3.5" />}
                      disabled={identity === 'caller'}
                      onClick={() => void onIdentity?.(id, 'caller')}
                    >
                      <span>
                        <span className="block">Caller 模式</span>
                        <span className="block font-normal text-muted-foreground">使用当前对话用户身份调用</span>
                      </span>
                    </Button>
                  </PopoverContent>
                </Popover>
              ) : kind === 'mcp' ? (
                <TooltipProvider delayDuration={0}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span data-testid={`mcp-identity-disabled-${id}`} className="inline-flex" tabIndex={0}>
                        <Button
                        variant="ghost"
                        size="icon"
                        className="size-5 shrink-0 p-0 text-muted-foreground hover:bg-transparent"
                        disabled
                        aria-label={`${item.name}访问方式不可切换`}
                        leftIcon={<UserRound className="size-3.5" />}
                      />
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>{identityDisabledReason || '当前 Bot 不支持切换 MCP 访问方式'}</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ) : null}
              {kind !== 'cli' ? (
                <ConfirmDialog
                  title={`确认移除该 ${kind === 'skill' ? 'Skill' : 'MCP'}？`}
                  description={`移除「${item.name}」后，当前能力集将不再使用该能力。`}
                  confirmText="确认移除"
                  confirmVariant="destructive"
                  disabled={!editable || Boolean(removingId)}
                  onConfirm={async () => {
                    setRemovingId(id);
                    try {
                      await onRemove?.(id);
                    } finally {
                      setRemovingId(undefined);
                    }
                  }}
                >
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-5 shrink-0 p-0 text-destructive opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                    aria-label={`移除${item.name}`}
                    leftIcon={
                      removingId === id ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="size-3.5" />
                      )
                    }
                  />
                </ConfirmDialog>
              ) : null}
            </div>
          );
          })}
        </div>
      ) : (
        <div className="flex h-[104px] flex-col items-center justify-center rounded-xl bg-muted/30 text-xs text-muted-foreground">
          <FileText className="mb-2 size-7 text-muted-foreground/40" aria-hidden />
          暂无数据
        </div>
      )}
    </div>
  );
}
