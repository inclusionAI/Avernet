/**
 * useFriends - 好友关系管理 Hook
 *
 * 遵循三层架构：Hook层封装业务逻辑、API调用、错误处理和Toast
 */

import * as BcnController from '@/services/backend-api/BcnController';
import { computeIsOnline } from '@/stores/botNetworkStore';
import {
  useFriendStore,
  type FriendInfo,
  type FriendRequest,
  type FriendRequestDirection,
} from '@/stores/friendStore';
import { handleNetworkError } from '@/utils/hooksErrorHandler';
import { retryOnTransient } from '@/utils/retryRequest';
import { useCallback } from 'react';
import { toast } from 'sonner';

export function useFriends() {
  const {
    friends,
    isLoadingFriends,
    receivedRequests,
    sentRequests,
    isLoadingRequests,
    unreadRequestCount,
    isSendingRequest,
    isAcceptingRequest,
    isRejectingRequest,
    setFriends,
    addFriend,
    setReceivedRequests,
    setSentRequests,
    addSentRequest,
    updateRequest,
    setUnreadRequestCount,
    decrementUnreadCount,
    setLoadingFriends,
    setLoadingRequests,
    setSendingRequest,
    setAcceptingRequest,
    setRejectingRequest,
    areFriends,
    getFriendByUuid,
    getPendingReceivedCount,
    reset,
  } = useFriendStore();

  /**
   * 加载好友列表
   */
  const loadFriends = useCallback(
    async (botUuid: string): Promise<FriendInfo[]> => {
      try {
        setLoadingFriends(true);
        const response = await BcnController.getFriends({ bot_uuid: botUuid });

        if (response?.error) {
          throw new Error(response.error);
        }

        // 兼容不同的响应格式
        const friendList = response?.friends || response?.data || [];
        const formattedFriends: FriendInfo[] = friendList.map((f: any) => ({
          bot_uuid: f.bot_uuid,
          bot_name: f.name,
          summary: f.summary,
          is_online: computeIsOnline({
            status: f.status,
            visibility: f.visibility,
            dynamic_status: f.dynamic_status,
          }),
          dynamic_status: f.dynamic_status,
        }));

        setFriends(formattedFriends);
        return formattedFriends;
      } catch (error: any) {
        console.error('[useFriends] Failed to load friends:', error);
        handleNetworkError(error, {
          module: 'Friends',
          action: '加载好友列表',
        });
        return [];
      } finally {
        setLoadingFriends(false);
      }
    },
    [setLoadingFriends, setFriends],
  );

  /**
   * 加载好友请求
   *
   * 注意：后端接口 /bcn/friends/requests 返回格式为 {  [...], success: true }
   * 需要根据 direction 参数在客户端筛选收到的和发出的请求
   * 如果后端未返回 from_bot_name/to_bot_name，会调用 queryBots 批量查询填充
   */
  const loadRequests = useCallback(
    async (
      botUuid: string,
      direction: FriendRequestDirection = 'all',
    ): Promise<{ received: FriendRequest[]; sent: FriendRequest[] }> => {
      try {
        setLoadingRequests(true);
        // 网关瞬时 504 重试（GroupChat 进页面并发拉取，间歇超时重试通常即成功）
        const response = await retryOnTransient(
          () =>
            BcnController.getFriendRequests({
              bot_uuid: botUuid,
              direction,
            }),
          { retries: 1 },
        );

        if (response?.error) {
          throw new Error(response.error);
        }

        // 后端返回格式: { data: FriendRequestItem[], success: boolean }
        // 需要根据当前 botUuid 判断是收到还是发出的请求
        const allRequests: BcnController.FriendRequestItem[] =
          response?.data || [];

        // 收集缺失名字的 bot_uuid
        const missingNameUuids = new Set<string>();
        allRequests.forEach((r) => {
          if (!r.from_bot_name) missingNameUuids.add(r.from_bot);
          if (!r.to_bot_name) missingNameUuids.add(r.to_bot);
        });

        // 批量查询缺失的名字
        let botNameMap = new Map<string, string>();
        if (missingNameUuids.size > 0) {
          try {
            const botInfos = await BcnController.queryBots({
              bot_uuids: Array.from(missingNameUuids),
            });
            botInfos.forEach((bot) => {
              if (bot.capabilities?.name) {
                botNameMap.set(bot.bot_uuid, bot.capabilities.name);
              }
            });
          } catch (e) {
            console.warn('[useFriends] Failed to query bot names:', e);
          }
        }

        const formatRequest = (
          r: BcnController.FriendRequestItem,
        ): FriendRequest => ({
          id: r.id,
          from_bot: r.from_bot,
          to_bot: r.to_bot,
          status: r.status as 'pending' | 'accepted' | 'rejected',
          created_at: r.created_at,
          updated_at: r.updated_at,
          // 优先使用后端返回的名字，否则使用 queryBots 查询结果
          from_bot_name: r.from_bot_name || botNameMap.get(r.from_bot),
          to_bot_name: r.to_bot_name || botNameMap.get(r.to_bot),
        });

        // 根据 from_bot/to_bot 区分收到和发出的请求
        const received: FriendRequest[] = allRequests
          .filter((r) => r.to_bot === botUuid)
          .map(formatRequest);
        const sent: FriendRequest[] = allRequests
          .filter((r) => r.from_bot === botUuid)
          .map(formatRequest);

        if (direction === 'received' || direction === 'all') {
          setReceivedRequests(received);
        }
        if (direction === 'sent' || direction === 'all') {
          setSentRequests(sent);
        }

        // 计算未读数量（pending 状态的收到请求）
        const pendingReceived = received.filter((r) => r.status === 'pending');
        setUnreadRequestCount(pendingReceived.length);

        return { received, sent };
      } catch (error: any) {
        console.error('[useFriends] Failed to load requests:', error);
        handleNetworkError(error, {
          module: 'Friends',
          action: '加载好友请求',
        });
        return { received: [], sent: [] };
      } finally {
        setLoadingRequests(false);
      }
    },
    [
      setLoadingRequests,
      setReceivedRequests,
      setSentRequests,
      setUnreadRequestCount,
    ],
  );

  /**
   * 发送好友请求
   */
  const sendFriendRequest = useCallback(
    async (fromBot: string, toBot: string): Promise<boolean> => {
      try {
        setSendingRequest(true);
        const response = await BcnController.createFriendRequest({
          from_bot: fromBot,
          to_bot: toBot,
        });

        if (response?.error) {
          throw new Error(response.error);
        }

        // 添加到已发送列表
        if (response?.id) {
          addSentRequest({
            id: response.id,
            from_bot: fromBot,
            to_bot: toBot,
            status: 'pending',
            created_at: Date.now(),
            updated_at: Date.now(),
          });
        }

        toast.success('好友请求已发送');
        return true;
      } catch (error: any) {
        console.error('[useFriends] Failed to send request:', error);
        handleNetworkError(error, {
          module: 'Friends',
          action: '发送好友请求',
        });
        return false;
      } finally {
        setSendingRequest(false);
      }
    },
    [setSendingRequest, addSentRequest],
  );

  /**
   * 接受好友请求
   */
  const acceptRequest = useCallback(
    async (requestId: string): Promise<boolean> => {
      try {
        setAcceptingRequest(true);
        const response = await BcnController.acceptFriendRequest({
          request_id: requestId,
        });

        if (response?.error) {
          throw new Error(response.error);
        }

        // 更新请求状态
        updateRequest(requestId, { status: 'accepted' });
        decrementUnreadCount();

        // ���加到好友列表
        if (response?.friend) {
          addFriend({
            bot_uuid: response.friend.bot_uuid,
            bot_name: response.friend.bot_name,
            is_online: true,
          });
        }

        toast.success('已接受好友请求');
        return true;
      } catch (error: any) {
        console.error('[useFriends] Failed to accept request:', error);
        handleNetworkError(error, {
          module: 'Friends',
          action: '接受好友请求',
        });
        return false;
      } finally {
        setAcceptingRequest(false);
      }
    },
    [setAcceptingRequest, updateRequest, decrementUnreadCount, addFriend],
  );

  /**
   * 拒绝好友请求
   */
  const rejectRequest = useCallback(
    async (requestId: string): Promise<boolean> => {
      try {
        setRejectingRequest(true);
        const response = await BcnController.rejectFriendRequest({
          request_id: requestId,
        });

        if (response?.error) {
          throw new Error(response.error);
        }

        // 更新请求状态
        updateRequest(requestId, { status: 'rejected' });
        decrementUnreadCount();

        toast.success('已拒绝好友请求');
        return true;
      } catch (error: any) {
        console.error('[useFriends] Failed to reject request:', error);
        handleNetworkError(error, {
          module: 'Friends',
          action: '拒绝好友请求',
        });
        return false;
      } finally {
        setRejectingRequest(false);
      }
    },
    [setRejectingRequest, updateRequest, decrementUnreadCount],
  );

  /**
   * 检查两个 Bot 是否为好友
   */
  const checkFriendship = useCallback(
    (botA: string, botB: string): boolean => {
      return areFriends(botA, botB);
    },
    [areFriends],
  );

  return {
    // === State ===
    friends,
    isLoadingFriends,
    receivedRequests,
    sentRequests,
    isLoadingRequests,
    unreadRequestCount,
    isSendingRequest,
    isAcceptingRequest,
    isRejectingRequest,

    // === Actions ===
    loadFriends,
    loadRequests,
    sendFriendRequest,
    acceptRequest,
    rejectRequest,
    checkFriendship,

    // === Helpers ===
    getFriendByUuid,
    getPendingReceivedCount,
    reset,
  };
}
