/**
 * RequestsTab - 新的好友请求 Tab
 *
 * 处理待处理的好友请求，内部调用 useFriends hook
 */

import BotAvatar from '@/components/BotAvatar';
import Button from '@/components/Button';
import Empty from '@/components/Empty';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import { Loader2, UserPlus } from 'lucide-react';
import React, { useCallback, useEffect, useMemo } from 'react';
import { useFriends } from '../hooks/useFriends';

const RequestsTab: React.FC = () => {
  const driverBot = useBotNetworkStore((state) => state.driverBot);

  const {
    receivedRequests,
    isLoadingRequests,
    isAcceptingRequest,
    isRejectingRequest,
    loadFriends,
    loadRequests,
    acceptRequest,
    rejectRequest,
  } = useFriends();

  // 组件挂载时加载好友请求数据
  useEffect(() => {
    if (driverBot?.bot_uuid && driverBot?.visibility !== 'offline') {
      loadRequests(driverBot.bot_uuid, 'received');
    }
  }, [driverBot?.bot_uuid, loadRequests]);

  // 新的好友请求（pending 状态）
  const pendingRequests = useMemo(
    () => receivedRequests.filter((r) => r.status === 'pending'),
    [receivedRequests],
  );

  // 接受好友请求
  const handleAccept = useCallback(
    async (requestId: string) => {
      const success = await acceptRequest(requestId);
      if (success && driverBot?.bot_uuid) {
        await loadFriends(driverBot.bot_uuid);
      }
    },
    [acceptRequest, loadFriends, driverBot?.bot_uuid],
  );

  // 拒绝好友请求
  const handleReject = useCallback(
    async (requestId: string) => {
      await rejectRequest(requestId);
    },
    [rejectRequest],
  );

  return (
    <div className="p-4">
      {isLoadingRequests ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
        </div>
      ) : pendingRequests.length === 0 ? (
        <Empty
          size="sm"
          icon={<UserPlus />}
          title="暂无新的好友请求"
          description="新的好友请求会显示在这里"
          className="py-12"
        />
      ) : (
        <div className="space-y-3">
          {pendingRequests.map((request) => (
            <div
              key={request.id}
              className="flex items-center gap-3 px-3 py-3 bg-slate-50 rounded-lg"
            >
              <BotAvatar
                type="expert"
                size="sm"
                name={request.from_bot_name || request.from_bot}
                botId={request.from_bot?.split(':')[0]}
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-700 truncate">
                  {request.from_bot_name || request.from_bot}
                </p>
                <p className="text-xs text-slate-400">请求添加为好友</p>
              </div>
              <div className="flex items-center gap-1.5">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => handleAccept(request.id)}
                  loading={isAcceptingRequest}
                  data-aspm-click="ca114903.da194217"
                  data-aspm-desc="GroupChat-接受好友请求"
                  data-aspm-param={``}
                  data-aspm-expo
                >
                  接受
                </Button>
                <Button
                  variant="danger"
                  ghost
                  size="sm"
                  onClick={() => handleReject(request.id)}
                  loading={isRejectingRequest}
                  className="border border-red-300 text-red-600 hover:bg-red-50 hover:text-red-700"
                  data-aspm-click="ca114903.da194218"
                  data-aspm-desc="GroupChat-拒绝好友请求"
                  data-aspm-param={``}
                  data-aspm-expo
                >
                  拒绝
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default RequestsTab;
