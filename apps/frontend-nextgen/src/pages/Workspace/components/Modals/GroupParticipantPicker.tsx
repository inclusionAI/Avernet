import { Button, Empty, Input, Segmented, Skeleton } from '@/components/ui';
import type { CollaborationBotView } from '@/services/workspace/collaborationCandidateService';
import { cn } from '@/utils/cn';
import { Search } from 'lucide-react';
import { useMemo } from 'react';
import type { CandidateFilterMode, UseGroupCollaborationPickerResult } from '../../hooks/useGroupCollaborationPicker';
import type { useServiceBotRouting } from '../../hooks/useServiceBotRouting';
import { ParticipantRow } from './ParticipantRow';
import { SelectedParticipantChip } from './SelectedParticipantChip';

export interface GroupParticipantPickerProps {
  picker: UseGroupCollaborationPickerResult;
  selectedIds: string[];
  selectedOptions: Array<{ id: string; name: string; avatarUrl?: string }>;
  showMineTab: boolean;
  cardMode?: boolean;
  onToggle: (id: string) => void;
  excludeId?: string | null;
  /** 置顶 Bot（如发起方 bot）；在 task_dag 模式下将此 Bot 放到列表首位。 */
  prependBot?: { id: string; name: string } | null;
  /** 候选列表滚动视口的局部高度覆盖。 */
  listViewportClassName?: string;
  /** 在固定高度面板中占满 Footer 上方的剩余空间。 */
  fillAvailableHeight?: boolean;
  routing?: ReturnType<typeof useServiceBotRouting>;
  /** 其他协作角色已绑定的 Bot：保留路由配置入口，不改变当前角色的选择。 */
  additionalRoutingOptions?: Array<{ id: string; name: string; avatarUrl?: string }>;
}

function matchesSearch(bot: CollaborationBotView, keyword: string): boolean {
  const q = keyword.trim().toLowerCase();
  if (!q) return true;
  return [bot.name, bot.summary ?? ''].some((text) => text.toLowerCase().includes(q));
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
  listViewportClassName,
  fillAvailableHeight = false,
  routing,
  additionalRoutingOptions = [],
}: GroupParticipantPickerProps) {
  // 画像推荐态：可协作 Bot Tab + 按画像筛选 + 关键词非空时，列表切换为语义推荐结果。
  const recommendActive =
    picker.tab === 'candidates' && picker.filterMode === 'profile' && picker.search.trim().length > 0;

  const visibleBots = useMemo(() => {
    const source = recommendActive
      ? picker.recommendations
      : picker.tab === 'friends'
      ? picker.friends
      : picker.tab === 'mine'
      ? picker.mine
      : picker.candidates;
    const filtered = source.filter(
      (bot) => bot.id !== excludeId && (recommendActive || matchesSearch(bot, picker.search)),
    );
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
  }, [excludeId, prependBot, picker.candidates, picker.friends, picker.mine, picker.recommendations, picker.search, picker.tab, recommendActive]);

  const isLoading = recommendActive
    ? picker.isLoadingRecommend
    : picker.tab === 'friends'
    ? picker.isLoadingFriends
    : picker.tab === 'mine'
    ? picker.isLoadingMine
    : picker.isLoadingCandidates;
  const selectedBots = selectedOptions.filter((bot) => selectedIds.includes(bot.id));
  const additionalBots = additionalRoutingOptions.filter(
    (bot) => !selectedIds.includes(bot.id) && routing?.states[bot.id]?.status !== 'non_service',
  );

  return (
    <div
      data-testid="group-participant-picker"
      className={cn('w-full min-w-0 max-w-full', fillAvailableHeight && 'flex min-h-0 flex-1 flex-col')}
    >
      <div className="mb-2 flex shrink-0 items-center justify-between">
        <span className="text-xs font-semibold text-foreground">
          成员 Bot <span className="text-destructive">*</span>
        </span>
        <span className="rounded-lg border border-primary/20 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
          已选 {selectedIds.length} 个
        </span>
      </div>

      {(selectedBots.length > 0 || additionalBots.length > 0) && (
        <div
          data-testid="group-participant-selected"
          className="app-scrollbar mb-3 flex max-h-24 min-w-0 max-w-full shrink-0 flex-wrap gap-2 overflow-y-auto pr-1"
        >
          {selectedBots.map((bot) => (
            <SelectedParticipantChip
              key={bot.id}
              bot={bot}
              routing={routing}
              onRemove={bot.id === excludeId ? undefined : () => onToggle(bot.id)}
            />
          ))}
          {additionalBots.map((bot) => (
            <SelectedParticipantChip key={bot.id} bot={bot} routing={routing} />
          ))}
        </div>
      )}

      <div className="mb-3 shrink-0">
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

      {picker.tab === 'candidates' && (
        <div className="mb-3 shrink-0">
          <Segmented<CandidateFilterMode>
            value={picker.filterMode}
            onChange={picker.setFilterMode}
            options={[
              { value: 'name', label: '按名称筛选' },
              { value: 'profile', label: '按画像筛选' },
            ]}
            className="w-full min-w-0 rounded-lg"
          />
        </div>
      )}

      <div className="relative mb-3 shrink-0">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="h-9 rounded-md pl-9"
          value={picker.search}
          onChange={(event) => picker.setSearch(event.target.value)}
          placeholder={
            picker.tab === 'candidates' && picker.filterMode === 'profile'
              ? '输入画像关键词，如：值班、数据分析'
              : '搜索 Bot 名称或描述...'
          }
          aria-label={
            picker.tab === 'candidates' && picker.filterMode === 'profile'
              ? '按画像关键词搜索可协作 Bot'
              : '搜索 Bot 名称或描述'
          }
        />
      </div>

      {recommendActive && picker.recommendError ? (
        <div className="flex items-center justify-between rounded-lg bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <span>{picker.recommendError}</span>
          <Button variant="ghost" size="sm" className="border-0" onClick={picker.retryRecommend}>
            重试
          </Button>
        </div>
      ) : picker.error ? (
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
              recommendActive
                ? '暂无与该目标相关的可协作 Bot'
                : picker.tab === 'friends'
                ? '暂无好友 Bot'
                : picker.tab === 'mine'
                ? '暂无已管理 Bot'
                : '暂无可协作 Bot'
            }
            description={
              recommendActive
                ? '清除关键词后恢复原可协作 Bot 列表。'
                : picker.search
                ? '试试调整搜索词。'
                : '可切换到另一个 Tab 查看候选。'
            }
          />
        </div>
      ) : (
        <div
          className={cn(
            'min-w-0 w-full max-w-full overflow-hidden rounded-lg border border-border bg-card',
            cardMode && 'border-0 bg-transparent',
            fillAvailableHeight && 'min-h-0 flex-1',
          )}
        >
          <div
            data-testid="group-participant-list"
            className={cn(
              'app-scrollbar min-h-[180px] max-h-[320px] overflow-y-auto pb-2',
              cardMode ? 'space-y-1.5' : 'divide-y divide-divider',
              fillAvailableHeight && 'h-full max-h-none',
              listViewportClassName,
            )}
            onScroll={(event) => {
              const target = event.currentTarget;
              if (target.scrollHeight - target.scrollTop - target.clientHeight < 40) picker.loadMore();
            }}
          >
            {visibleBots.map((bot) => (
              <ParticipantRow
                key={bot.id}
                bot={bot}
                selected={selectedIds.includes(bot.id)}
                recommendActive={recommendActive}
                isOriginator={Boolean(prependBot && bot.id === prependBot.id)}
                cardMode={cardMode}
                onToggle={onToggle}
              />
            ))}
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
