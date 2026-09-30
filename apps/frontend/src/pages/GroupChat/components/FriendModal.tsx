/**
 * FriendModal - 添加好友弹窗
 *
 * 包含三个 Tab：
 * - 已添加好友：展示当前 Bot 的好友列表
 * - 推荐好友：搜索并添加新好友
 * - 新的好友：处理待处理的好友请求
 */

import { useActor } from '@/hooks/useActor';
import type { ActorBot } from '@/services/backend-api/ActorController';
import type { DiscoverBotInfo } from '@/stores/botNetworkStore';
import { computeIsOnline, useBotNetworkStore } from '@/stores/botNetworkStore';
import { cn } from '@/utils/utils';
import { Heart, MessageSquare, UserPlus, X } from 'lucide-react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useFriends } from '../hooks/useFriends';
import FriendsTab from './FriendsTab';
import RecommendTab from './RecommendTab';
import RequestsTab from './RequestsTab';

export type FriendModalTab = 'friends' | 'recommend' | 'requests';

interface FriendModalProps {
  open: boolean;
  onClose: () => void;
  /** 初始 Tab，默认 'friends' */
  defaultTab?: FriendModalTab;
}

const FriendModal: React.FC<FriendModalProps> = ({
  open,
  onClose,
  defaultTab = 'friends',
}) => {
  // 从 store 获取 driverBot
  const driverBot = useBotNetworkStore((state) => state.driverBot);

  // 内部调用 hooks
  const {
    friends,
    isLoadingFriends,
    receivedRequests,
    loadFriends,
    loadRequests,
    sendFriendRequest,
  } = useFriends();

  // Actor hook - 直接调用 API
  const { loadActors } = useActor();

  // ===== 推荐好友状态管理（使用 Actor API）=====
  const [recommendedBots, setRecommendedBots] = useState<DiscoverBotInfo[]>([]);
  const [isLoadingRecommended, setIsLoadingRecommended] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMoreRecommended, setHasMoreRecommended] = useState(true);
  const [pagination, setPagination] = useState({
    pageNo: 1,
    pageSize: 20,
    total: 0,
  });
  const listRef = useRef<HTMLDivElement>(null);
  const currentSearchQuery = useRef<string>('');

  // 映射 ActorBot 到 DiscoverBotInfo
  const mapToDiscoverBotInfo = (bots: ActorBot[]): DiscoverBotInfo[] =>
    bots.map((bot) => ({
      ...bot,
      bot_name: bot.bot_name || bot.capabilities?.name || bot.bot_uuid,
      summary: bot.summary ?? bot.capabilities?.description ?? undefined,
      visibility: (bot.visibility as DiscoverBotInfo['visibility']) ?? 'public',
      is_online: computeIsOnline({
        status: bot.status,
        visibility:
          (bot.visibility as DiscoverBotInfo['visibility']) ?? 'public',
        dynamic_status: bot.dynamic_status,
      }),
    }));

  // 加载推荐好友列表
  const loadRecommendedBots = useCallback(
    async (params?: { name?: string; pageNo?: number; append?: boolean }) => {
      const currentBotUuid = driverBot?.bot_uuid;
      if (!currentBotUuid) return;

      const isAppend = params?.append ?? false;
      const pageNo = params?.pageNo ?? 1;

      if (pageNo === 1) {
        setIsLoadingRecommended(true);
      } else {
        setIsLoadingMore(true);
      }

      try {
        const response = await loadActors({
          currentBotUuid,
          cooperatableOnly: false,
          pageNo,
          pageSize: pagination.pageSize,
          name: params?.name,
          append: isAppend,
        });

        const bots = mapToDiscoverBotInfo(response.bots || []);

        if (isAppend) {
          setRecommendedBots((prev) => [...prev, ...bots]);
        } else {
          setRecommendedBots(bots);
        }

        setPagination((prev) => ({
          ...prev,
          pageNo,
          total: response.total ?? 0,
        }));

        // 判断是否还有更多
        setHasMoreRecommended(
          bots.length === pagination.pageSize &&
            (pageNo - 1) * pagination.pageSize + bots.length < response.total,
        );
      } catch (error) {
        console.error('[FriendModal] Failed to load recommended bots:', error);
      } finally {
        setIsLoadingRecommended(false);
        setIsLoadingMore(false);
      }
    },
    [driverBot?.bot_uuid, loadActors, pagination.pageSize],
  );

  // 加载更多推荐好友（滚动触发）
  const loadMoreRecommendedBots = useCallback(() => {
    if (isLoadingRecommended || isLoadingMore || !hasMoreRecommended) return;
    loadRecommendedBots({
      name: currentSearchQuery.current || undefined,
      pageNo: pagination.pageNo + 1,
      append: true,
    });
  }, [
    isLoadingRecommended,
    isLoadingMore,
    hasMoreRecommended,
    pagination.pageNo,
    loadRecommendedBots,
  ]);

  // 内部状态
  const [activeTab, setActiveTab] = useState<FriendModalTab>(defaultTab);

  // 弹窗打开时加载数据
  useEffect(() => {
    if (open && driverBot?.bot_uuid) {
      if (activeTab === 'friends') {
        loadFriends(driverBot.bot_uuid);
      } else if (activeTab === 'recommend') {
        // 重置状态并加载第一页
        currentSearchQuery.current = '';
        setPagination({ pageNo: 1, pageSize: 20, total: 0 });
        loadRecommendedBots({ pageNo: 1 });
      }
    }
  }, [
    open,
    driverBot?.bot_uuid,
    loadFriends,
    loadRequests,
    activeTab,
    loadRecommendedBots,
  ]);

  // 好友 UUID 集合（用于过滤）
  const friendUuids = useMemo(
    () => new Set<string>(friends.map((f: { bot_uuid: string }) => f.bot_uuid)),
    [friends],
  );

  // 新的好友请求数量
  const pendingRequestCount = useMemo(
    () =>
      receivedRequests.filter((r: { status: string }) => r.status === 'pending')
        .length,
    [receivedRequests],
  );

  // 搜索推荐好友
  const handleSearch = useCallback(
    (query: string) => {
      currentSearchQuery.current = query;
      loadRecommendedBots({ name: query || undefined, pageNo: 1 });
    },
    [loadRecommendedBots],
  );

  // 滚动加载更多
  const handleScroll = useCallback(() => {
    if (!listRef.current || isLoadingRecommended || isLoadingMore) return;

    const el = listRef.current;
    const scrollBottom = el.scrollTop + el.clientHeight;
    const threshold = el.scrollHeight - 60;

    if (scrollBottom >= threshold && hasMoreRecommended) {
      loadMoreRecommendedBots();
    }
  }, [
    isLoadingRecommended,
    isLoadingMore,
    hasMoreRecommended,
    loadMoreRecommendedBots,
  ]);

  // 自动加载更多：当推荐好友列表内容未填满容器或接近底部时，自动继续加载
  useEffect(() => {
    const checkAndLoadMore = () => {
      // 只在添加好友Tab下执行
      if (activeTab !== 'recommend' || !listRef.current) return;

      const container = listRef.current;

      // 距离底部阈值（像素）
      const threshold = 100;
      const { scrollHeight, clientHeight, scrollTop } = container;
      const distanceToBottom = scrollHeight - scrollTop - clientHeight;

      // 两种情况触发加载：
      // 1. 无滚动条（内容未填满容器）
      // 2. 有滚动条但距离底部小于阈值（接近底部）
      const shouldLoadMore =
        scrollHeight <= clientHeight || distanceToBottom < threshold;

      if (
        shouldLoadMore &&
        hasMoreRecommended &&
        !isLoadingRecommended &&
        !isLoadingMore &&
        recommendedBots.length > 0
      ) {
        loadMoreRecommendedBots();
      }
    };

    // 延迟执行，确保 DOM 已更新
    const timer = setTimeout(checkAndLoadMore, 100);
    return () => clearTimeout(timer);
  }, [
    activeTab,
    recommendedBots,
    hasMoreRecommended,
    isLoadingRecommended,
    isLoadingMore,
    loadMoreRecommendedBots,
  ]);

  // 发送好友请求
  const handleSendRequest = useCallback(
    async (toBot: string) => {
      if (!driverBot?.bot_uuid) return;
      const success = await sendFriendRequest(driverBot.bot_uuid, toBot);
      if (success) {
        await loadFriends(driverBot.bot_uuid);
      }
    },
    [driverBot?.bot_uuid, sendFriendRequest, loadFriends],
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 遮罩 */}
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
        data-aspm-click="ca114903.da194184"
        data-aspm-desc="GroupChat-关闭好友弹窗遮罩"
        data-aspm-param={``}
        data-aspm-expo
      />

      {/* 弹窗内容 */}
      <div className="relative w-full max-w-lg mx-4 bg-white rounded-xl shadow-xl flex flex-col h-[90vh]">
        {/* 头部 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 flex-shrink-0">
          <h2 className="text-base font-semibold text-slate-800">添加好友</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
            data-aspm-click="ca114903.da194185"
            data-aspm-desc="GroupChat-关闭好友弹窗"
            data-aspm-param={``}
            data-aspm-expo
          >
            <X className="w-4 h-4 text-slate-400" />
          </button>
        </div>

        {/* Tab 切换 */}
        <div className="px-6 border-b border-slate-100 flex-shrink-0">
          <div className="flex gap-6">
            <button
              type="button"
              onClick={() => setActiveTab('friends')}
              className={cn(
                'flex items-center gap-2 py-3 text-sm font-medium border-b-2 transition-colors',
                activeTab === 'friends'
                  ? 'text-lavender-600 border-lavender-600'
                  : 'text-slate-500 border-transparent hover:text-slate-700',
              )}
              data-aspm-click="ca114903.da194186"
              data-aspm-desc="GroupChat-切换到我的好友"
              data-aspm-param={``}
              data-aspm-expo
            >
              <Heart className="w-4 h-4" />
              我的好友
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('recommend')}
              className={cn(
                'flex items-center gap-2 py-3 text-sm font-medium border-b-2 transition-colors',
                activeTab === 'recommend'
                  ? 'text-lavender-600 border-lavender-600'
                  : 'text-slate-500 border-transparent hover:text-slate-700',
              )}
              data-aspm-click="ca114903.da194187"
              data-aspm-desc="GroupChat-切换到添加好友"
              data-aspm-param={``}
              data-aspm-expo
            >
              <MessageSquare className="w-4 h-4" />
              添加好友
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('requests')}
              className={cn(
                'flex items-center gap-2 py-3 text-sm font-medium border-b-2 transition-colors',
                activeTab === 'requests'
                  ? 'text-lavender-600 border-lavender-600'
                  : 'text-slate-500 border-transparent hover:text-slate-700',
              )}
              data-aspm-click="ca114903.da194188"
              data-aspm-desc="GroupChat-切换到好友申请"
              data-aspm-param={``}
              data-aspm-expo
            >
              <UserPlus className="w-4 h-4" />
              好友申请
              {pendingRequestCount > 0 && (
                <span className="text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded">
                  {pendingRequestCount}
                </span>
              )}
            </button>
          </div>
        </div>

        {/* Tab 内容 */}
        <div
          ref={activeTab === 'recommend' ? listRef : undefined}
          onScroll={activeTab === 'recommend' ? handleScroll : undefined}
          className="flex-1 overflow-y-auto"
        >
          {activeTab === 'friends' && (
            <FriendsTab friends={friends} isLoading={isLoadingFriends} />
          )}

          {activeTab === 'recommend' && (
            <RecommendTab
              recommendedBots={recommendedBots}
              isLoading={isLoadingRecommended}
              isLoadingMore={isLoadingMore}
              hasMore={hasMoreRecommended}
              pageNo={pagination.pageNo}
              pageSize={pagination.pageSize}
              total={pagination.total}
              driverBotUuid={driverBot?.bot_uuid}
              friendUuids={friendUuids}
              onSendRequest={handleSendRequest}
              onSearch={handleSearch}
            />
          )}

          {activeTab === 'requests' && <RequestsTab />}
        </div>
      </div>
    </div>
  );
};

export default FriendModal;
