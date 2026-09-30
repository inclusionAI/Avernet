/**
 * RecommendTab - 推荐好友 Tab
 *
 * 搜索并添加新好友，支持防抖搜索
 */

import BotAvatar from '@/components/BotAvatar';
import Button from '@/components/Button';
import Empty from '@/components/Empty';
import GoldBadge from '@/components/GoldBadge';
import { SearchInput } from '@/components/ui/search-input';
import type { DiscoverBotInfo } from '@/stores/botNetworkStore';
import { getBotRecommendTracker } from '@/utils/botRecommendTracker';
import { cn } from '@/utils/utils';
import { Loader2, MessageSquare } from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';

interface RecommendTabProps {
  recommendedBots: DiscoverBotInfo[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  pageNo: number;
  pageSize: number;
  total: number;
  driverBotUuid: string | undefined;
  friendUuids: Set<string>;
  onSendRequest: (botUuid: string) => Promise<void>;
  onSearch: (query: string) => void;
}

const RecommendTab: React.FC<RecommendTabProps> = ({
  recommendedBots: recommendedBotsProp,
  isLoading,
  isLoadingMore,
  hasMore,
  pageNo,
  pageSize,
  total,
  driverBotUuid,
  friendUuids,
  onSendRequest,
  onSearch,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [sendingRequestTo, setSendingRequestTo] = useState<string | null>(null);

  // 防抖定时器
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  // 标记是否已初始化，避免挂载时触发空搜索
  const hasInitializedRef = useRef(false);
  // 埋点 tracker
  const trackerRef = useRef(getBotRecommendTracker());

  // 防抖搜索
  const debouncedSearch = useCallback(
    (query: string) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      debounceTimerRef.current = setTimeout(() => {
        onSearch(query);
      }, 300);
    },
    [onSearch],
  );

  // 搜索框变化时触发防抖搜索（跳过初始挂载）
  useEffect(() => {
    if (!hasInitializedRef.current) {
      hasInitializedRef.current = true;
      return;
    }
    debouncedSearch(searchQuery);
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [searchQuery, debouncedSearch]);

  // 推荐好友（排除自己和已是好友的）
  const recommendedBots = recommendedBotsProp.filter((bot) => {
    if (bot.bot_uuid === driverBotUuid) return false;
    return !friendUuids.has(bot.bot_uuid);
  });

  // 发送好友请求
  const handleSendRequest = async (botUuid: string, position: number) => {
    // bot_select 埋点：添加 Bot
    trackerRef.current.onBotSelect(botUuid, 'add', position);

    setSendingRequestTo(botUuid);
    await onSendRequest(botUuid);
    setSendingRequestTo(null);
  };

  return (
    <div className="p-4">
      {/* 搜索框 */}
      <div className="mb-3">
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder="搜索 Bot..."
        />
      </div>

      {/* 推荐列表 */}
      {isLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
        </div>
      ) : recommendedBots.length === 0 ? (
        <Empty
          size="sm"
          icon={<MessageSquare />}
          title="暂无推荐好友"
          description="搜索 Bot 名称查找好友"
          className="py-12"
        />
      ) : (
        <>
          <div className="divide-y divide-slate-100">
            {recommendedBots.map((bot, index) => (
              <div
                key={bot.bot_uuid}
                className="flex items-center gap-3 px-3 py-3 hover:bg-slate-50 rounded-lg"
              >
                <div className="relative">
                  <BotAvatar
                    type="expert"
                    size="sm"
                    name={bot.bot_name}
                    botId={bot.bot_uuid?.split(':')[0]}
                    avatarUrl={bot.avatar_url}
                  />
                  {/* 在线状态指示器 */}
                  <div
                    className={cn(
                      'absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-white',
                      bot.dynamic_status?.status === 'active'
                        ? 'bg-green-500'
                        : 'bg-slate-300',
                    )}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium text-slate-700 truncate">
                      {bot.bot_name}
                    </p>
                    <GoldBadge botInfo={bot as any} />
                  </div>
                  {bot.summary && (
                    <p className="text-xs text-slate-400 truncate">
                      {bot.summary}
                    </p>
                  )}
                </div>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => handleSendRequest(bot.bot_uuid, index)}
                  loading={sendingRequestTo === bot.bot_uuid}
                  className="!px-3"
                  data-aspm-click="ca114903.da194216"
                  data-aspm-desc="GroupChat-添加推荐好友"
                  data-aspm-param={``}
                  data-aspm-expo
                >
                  添加
                </Button>
              </div>
            ))}

            {/* 加载更多指示器 */}
            {isLoadingMore && (
              <div className="flex items-center justify-center py-4 gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                <span className="text-xs text-slate-500">加载更多...</span>
              </div>
            )}

            {/* 已加载全部提示 - 当页数超过总页数时才显示 */}
            {!isLoadingMore &&
              !hasMore &&
              pageNo > Math.floor(total / pageSize) &&
              total > 0 && (
                <div className="py-3 text-center text-xs text-slate-300">
                  已加载全部 {recommendedBots.length} 个推荐
                </div>
              )}
          </div>
        </>
      )}
    </div>
  );
};

export default RecommendTab;
