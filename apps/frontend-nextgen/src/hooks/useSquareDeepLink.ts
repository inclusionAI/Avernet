import { notifySuccess } from '@/components/ui/notify';
import { parseSquareDeepLink } from '@/domain/collaborationSquare/mapper';
import type { PublicGroup, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useSquareHighlightStore } from '@/stores/squareHighlightStore';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useRef } from 'react';

interface UseSquareDeepLinkOptions {
  resource: SquareResource;
  /** 分页标记的同步镜像：名称搜索结果分页未耗尽时深链持续加载再判定，避免重名目标落在后续页被误判失效。 */
  hasMoreRef: { current: boolean };
  loadMoreRef: { current: () => void };
  openGroupMembers: (group: PublicGroup) => Promise<void>;
  handleTargetInvalid: (resource: SquareResource, id: string) => void;
}

/**
 * 解析身份回退目标：公开受众（对用户公开 / 对 Bot 公开）是两套独立设置，与当前浏览身份
 * 不匹配时该 Bot 在列表搜索中不可见。当前为用户身份 → 回退到第一个 Bot 身份；
 * 当前为 Bot 身份 → 回退到登录用户身份。返回 null 表示无可用回退身份（如用户名下没有 Bot）。
 */
function resolveIdentityFallback(): { id: string | null; message: string } | null {
  const { identities } = useWorkspaceStore.getState();
  const { selectedIdentityId } = useSquareIdentityStore.getState();
  if (selectedIdentityId === null) {
    const botIdentity = identities.find((identity) => identity.kind === 'bot');
    return botIdentity
      ? { id: botIdentity.id, message: '该 Bot 仅对 Bot 公开，已为你切换为 Bot 身份查看' }
      : null;
  }
  return { id: null, message: '该 Bot 仅对用户公开，已为你切换为用户身份查看' };
}

/**
 * bot/group 的 SquareDeepLink 深链解析。任务广场本期不接深链（Non-goal），resource='task' 时跳过。
 * Bot 深链按「列表内定位」落地：先把搜索对齐到分享名称（名称模式 + 关键词，列表请求带当前协作身份），
 * 列表按新条件加载完成后在结果中按 canonical ID 定位目标卡片并触发高亮；重名命中多页时持续
 * 加载直到命中或分页耗尽，仍未命中才按目标失效处理。
 * 不发起独立的深链请求：身份异步就绪、列表失败重试由 effect 依赖轮次自然重驱动，无中止竞态。
 */
export function useSquareDeepLink({
  resource,
  hasMoreRef,
  loadMoreRef,
  openGroupMembers,
  handleTargetInvalid,
}: UseSquareDeepLinkOptions) {
  const loading = useCollaborationSquareStore((state) => state.loading);
  const bots = useCollaborationSquareStore((state) => state.bots);
  const groups = useCollaborationSquareStore((state) => state.groups);
  const botQuery = useCollaborationSquareStore((state) => state.botQuery);
  const botSearchMode = useCollaborationSquareStore((state) => state.botSearchMode);
  const error = useCollaborationSquareStore((state) => state.error);
  const handledTarget = useRef<string | null>(null);
  // 搜索对齐后等待列表真正重载：防抖窗口内旧列表仍在，不能据其判定目标存在与否。
  const awaitingReload = useRef(false);
  const sawReloadLoading = useRef(false);
  // 本深链目标是否已尝试过一次身份回退（两类身份各定位一遍后不再重试）。
  const fallbackTried = useRef(false);

  useEffect(() => {
    if (resource === 'task' || typeof window === 'undefined') return;
    const deepLink = parseSquareDeepLink(window.location.search, resource);
    if (!deepLink) {
      handledTarget.current = null;
      awaitingReload.current = false;
      sawReloadLoading.current = false;
      fallbackTried.current = false;
      return;
    }
    const targetKey = `${deepLink.resource}:${deepLink.id}:${deepLink.searchHint ?? ''}`;
    if (handledTarget.current === targetKey) return;
    if (handledTarget.current !== null) fallbackTried.current = false;

    if (loading) {
      if (awaitingReload.current) sawReloadLoading.current = true;
      return;
    }

    if (resource === 'bot') {
      // 列表加载失败不算终态：等待重新加载/身份恢复后的下一轮再定位。
      if (error) {
        awaitingReload.current = false;
        sawReloadLoading.current = false;
        return;
      }
      // 分享链接声明了公开受众类型时，先匹配浏览身份（省去注定不可见的一轮搜索）。
      if (deepLink.audience) {
        const { selectedIdentityId, selectIdentity } = useSquareIdentityStore.getState();
        if (deepLink.audience === 'user') {
          if (selectedIdentityId !== null) {
            fallbackTried.current = true; // 受众类型已明确：定位失败也不再回切身份空跑
            notifySuccess('该 Bot 仅对用户公开，已为你切换为用户身份查看');
            selectIdentity(null);
            return;
          }
        } else if (selectedIdentityId === null) {
          const botIdentity = useWorkspaceStore.getState().identities.find((identity) => identity.kind === 'bot');
          if (!botIdentity) {
            // 目标仅对 Bot 公开且当前账号无 Bot 身份：必然不可见，直接按目标失效处理。
            handledTarget.current = targetKey;
            fallbackTried.current = false;
            handleTargetInvalid('bot', deepLink.id);
            return;
          }
          fallbackTried.current = true;
          notifySuccess('该 Bot 仅对 Bot 公开，已为你切换为 Bot 身份查看');
          selectIdentity(botIdentity.id);
          return;
        }
      }
      const { setQuery, setBotSearchMode } = useCollaborationSquareStore.getState();
      const { setHighlightBotId } = useSquareHighlightStore.getState();
      const searchHint = deepLink.searchHint?.trim();
      if (searchHint && (botSearchMode !== 'name' || botQuery !== searchHint)) {
        // 切换名称模式会清空关键词，须在其后再填入分享名称。
        if (botSearchMode !== 'name') setBotSearchMode('name');
        setQuery('bot', searchHint);
        awaitingReload.current = true;
        sawReloadLoading.current = false;
        return;
      }
      if (awaitingReload.current && !sawReloadLoading.current) return;
      awaitingReload.current = false;
      sawReloadLoading.current = false;
      const loadedBot = bots.find((item) => item.id === deepLink.id);
      if (loadedBot) {
        handledTarget.current = targetKey;
        fallbackTried.current = false;
        setHighlightBotId(deepLink.id);
        return;
      }
      // 重名分享名称可能命中多页结果：当前页未命中但分页未耗尽时先加载更多再判定，
      // 避免目标在后续页时被误判为不可见。经 ref 读取实时分页标记，规避 setHasMore
      // 批处理滞后于 setBots 同步通知的渲染轮次差。
      if (hasMoreRef.current) {
        void loadMoreRef.current();
        return;
      }
      // 未命中可能是公开受众与当前浏览身份不匹配（对用户公开/对 Bot 公开为独立设置），
      // 自动切换另一类身份并重试一次；两类身份都不可见（或无可用回退身份）才按目标失效处理。
      if (!fallbackTried.current) {
        const fallback = resolveIdentityFallback();
        if (fallback) {
          fallbackTried.current = true;
          notifySuccess(fallback.message);
          useSquareIdentityStore.getState().selectIdentity(fallback.id);
          return;
        }
      }
      handledTarget.current = targetKey;
      fallbackTried.current = false;
      handleTargetInvalid('bot', deepLink.id);
      return;
    }

    const group = groups.find((item) => item.id === deepLink.id);
    handledTarget.current = targetKey;
    if (group) void openGroupMembers(group);
    else handleTargetInvalid('group', deepLink.id);
  }, [botQuery, botSearchMode, bots, error, groups, handleTargetInvalid, loading, openGroupMembers, resource]);
}