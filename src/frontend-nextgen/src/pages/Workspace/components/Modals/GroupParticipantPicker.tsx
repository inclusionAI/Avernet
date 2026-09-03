import { Badge, Button, Empty, Input, Segmented, Skeleton } from '@/components/ui';
import type { CollaborationBotView } from '@/services/workspace/collaborationCandidateService';
import { cn } from '@/utils/cn';
import { Check, Plus, Search, X } from 'lucide-react';
import { useMemo } from 'react';
import type { UseGroupCollaborationPickerResult } from '../../hooks/useGroupCollaborationPicker';

export interface GroupParticipantPickerProps {
  picker: UseGroupCollaborationPickerResult;
  selectedIds: string[];
  selectedOptions: Array<{ id: string; name: string }>;
  showMineTab: boolean;
  cardMode?: boolean;
  onToggle: (id: string) => void;
  excludeId?: string | null;
  /** 置顶 Bot（如发起方 bot）；在 task_dag 模式下将此 Bot 放到列表首位。 */
  prependBot?: { id: string; name: string } | null;
}

function matchesSearch(bot: CollaborationBotView, keyword: string): boolean {
  const q = keyword.trim().toLowerCase();
  if (!q) return true;
  return [bot.name, bot.summary ?? ''].some((text) => text.toLowerCase().includes(q));
}

function avatarText(name: string): string {
  return name.trim().slice(0, 1).toUpperCase() || 'B';
}

/** 发起协作成员选择：好友/可协作 Bot 双 Tab + 名称搜索 + 已选 chips。 */
export function GroupParticipantPicker({
  picker,
  selectedIds,
  selectedOptions,
  showMineTab,
  cardMode = false,
  onToggle,
  excludeId,
  prependBot,
}: GroupParticipantPickerProps) {
  const visibleBots = useMemo(() => {
    const source = picker.tab === 'friends' ? picker.friends : picker.tab === 'mine' ? picker.mine : picker.candidates;
    const filtered = source.filter((bot) => bot.id !== excludeId && matchesSearch(bot, picker.search));
    if (!prependBot) return filtered;
    const originatorView: CollaborationBotView = {
      id: prependBot.id,
      name: prependBot.name,
      summary: '',
      online: true,
      status: 'online',
      reachability: 'reachable',
      visibility: 'public',
    };
    return [originatorView, ...filtered.filter((b) => b.id !== prependBot.id)];
  }, [excludeId, prependBot, picker.candidates, picker.friends, picker.mine, picker.search, picker.tab]);

  const isLoading =
    picker.tab === 'friends'
      ? picker.isLoadingFriends
      : picker.tab === 'mine'
      ? picker.isLoadingMine
      : picker.isLoadingCandidates;
  const selectedBots = selectedOptions.filter((bot) => selectedIds.includes(bot.id));

  return (
    <div data-testid="group-participant-picker" className="w-full min-w-0 max-w-full">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-foreground">
          成员 Bot <span className="text-destructive">*</span>
        </span>
        <span className="rounded-lg border border-primary/20 bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
          已选 {selectedIds.length} 个
        </span>
      </div>

      {selectedBots.length > 0 && (
        <div className="mb-3 flex min-w-0 max-w-full flex-wrap gap-2">
          {selectedBots.map((bot) => (
            <span
              key={bot.id}
              className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/10 py-1 pl-2 pr-1"
            >
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">
                {avatarText(bot.name)}
              </span>
              <span className="max-w-30 truncate text-xs font-semibold text-primary">{bot.name}</span>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`移除${bot.name}`}
                className="h-5 w-5 rounded-full border-0 p-0 text-primary hover:bg-primary/10"
                onClick={() => onToggle(bot.id)}
              >
                <X className="h-3 w-3" aria-hidden />
              </Button>
            </span>
          ))}
        </div>
      )}

      <div className="mb-3">
        <Segmented<UseGroupCollaborationPickerResult['tab']>
          value={picker.tab}
          onChange={picker.setTab}
          options={[
            ...(showMineTab ? [{ value: 'mine' as const, label: '已管理 Bot' }] : []),
            { value: 'friends', label: '好友 Bot' },
            { value: 'candidates', label: '可协作 Bot' },
          ]}
          className="w-full min-w-0 rounded-lg"
        />
      </div>

      <div className="relative mb-3">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="h-9 rounded-md pl-9"
          value={picker.search}
          onChange={(event) => picker.setSearch(event.target.value)}
          placeholder="搜索 Bot 名称或描述..."
          aria-label="搜索 Bot 名称或描述"
        />
      </div>

      {picker.tab === 'candidates' && (
        <div className="mb-3 rounded-lg border border-border bg-primary/10 px-3 py-2 text-xs leading-5 text-muted-foreground">
          可协作 Bot 范围：公开 Bot 与已接受好友的集合。
        </div>
      )}

      {picker.error ? (
        <div className="flex items-center justify-between rounded-lg bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <span>{picker.error}</span>
          <Button variant="ghost" size="sm" className="border-0" onClick={picker.retry}>
            重试
          </Button>
        </div>
      ) : isLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <Skeleton.Block key={i} className="h-14 w-full rounded-xl" />
          ))}
        </div>
      ) : visibleBots.length === 0 ? (
        <div className="rounded-lg border border-border bg-card">
          <Empty
            compact
            title={
              picker.tab === 'friends' ? '暂无好友 Bot' : picker.tab === 'mine' ? '暂无已管理 Bot' : '暂无可协作 Bot'
            }
            description={picker.search ? '试试调整搜索词。' : '可切换到另一个 Tab 查看候选。'}
          />
        </div>
      ) : (
        <div
          className={cn(
            'min-w-0 w-full max-w-full overflow-hidden rounded-lg border border-border bg-card',
            cardMode && 'border-0 bg-transparent',
          )}
        >
          <div
            className={cn(
              'app-scrollbar max-h-[320px] overflow-y-auto',
              cardMode ? 'space-y-1.5' : 'divide-y divide-border',
            )}
            onScroll={(event) => {
              const target = event.currentTarget;
              if (target.scrollHeight - target.scrollTop - target.clientHeight < 40) picker.loadMore();
            }}
          >
            {visibleBots.map((bot) => {
              const selected = selectedIds.includes(bot.id);
              const unknown = bot.detailsResolved === false;
              const unavailable = unknown || bot.reachability === 'unreachable' || !bot.online;
              const isOriginator = prependBot && bot.id === prependBot.id;
              return (
                <Button
                  key={bot.id}
                  type="button"
                  variant="ghost"
                  size="md"
                  disabled={unavailable}
                  aria-pressed={selected}
                  className={cn(
                    'h-auto min-w-0 w-full max-w-full justify-start gap-2 rounded-none border-0 px-3 py-2 text-left',
                    unknown
                      ? 'cursor-not-allowed bg-muted/50 text-muted-foreground'
                      : cardMode
                      ? selected
                        ? 'rounded-lg border border-primary bg-primary/10 hover:bg-primary/10'
                        : 'rounded-lg border border-border bg-card hover:border-primary/30 hover:bg-primary/10'
                      : selected
                      ? 'bg-primary/10 hover:bg-primary/10'
                      : 'hover:bg-muted/50',
                    unknown && cardMode && 'rounded-lg border border-border',
                  )}
                  onClick={() => onToggle(bot.id)}
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
                    {avatarText(bot.name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-semibold text-foreground">{bot.name}</span>
                      {isOriginator && (
                        <span className="shrink-0 rounded-full border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                          发起方
                        </span>
                      )}
                      <Badge tone={unavailable ? 'neutral' : 'primary'} className="flex shrink-0 items-center gap-1">
                        <i className={cn('h-1.5 w-1.5 rounded-full', bot.online ? 'bg-success' : 'bg-muted')} />
                        {unknown ? '未知' : bot.online ? '在线' : '隐身'}
                      </Badge>
                    </span>
                    {bot.summary && (
                      <span className="mt-1 block truncate text-xs text-muted-foreground">{bot.summary}</span>
                    )}
                  </span>
                  <span
                    className={cn(
                      'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border',
                      selected ? 'border-primary bg-primary' : 'border-primary/20 bg-card',
                    )}
                  >
                    {selected ? (
                      <Check className="h-4 w-4 text-primary-foreground" aria-hidden />
                    ) : (
                      <Plus className="h-4 w-4 text-primary" />
                    )}
                  </span>
                </Button>
              );
            })}
            {picker.isLoadingMore && (
              <div className="px-4 py-3">
                <Skeleton.Block className="h-6 w-full rounded-lg" />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default GroupParticipantPicker;
