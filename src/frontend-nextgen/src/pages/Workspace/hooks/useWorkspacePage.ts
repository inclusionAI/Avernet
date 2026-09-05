import type { IdentityView } from '@/domain/collaboration';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useSearchParams } from '@umijs/max';
import { useCallback, useEffect, useMemo, useRef } from 'react';

export type WorkspaceView = 'chat' | 'group';

export interface UseWorkspacePageResult {
  view: WorkspaceView;
  setView: (v: WorkspaceView) => void;
  activeIdentityId: string | null;
  identities: IdentityView[];
}

/**
 * useWorkspacePage 负责 URL ↔ Store 的双向同步：
 * - 读取 `tab=group` / `group=` / `session=` 并回填到 useWorkspaceStore；
 * - 视图切换时改写 `?tab=`；
 * - 首次挂载调用 workspaceService.initWorkspace 拉取可协作身份。
 *
 * 完整的页面装配（会话/消息接入）延迟到 Task 10 集成。
 */
export function useWorkspacePage(): UseWorkspacePageResult {
  const [searchParams, setSearchParams] = useSearchParams();

  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const identities = useWorkspaceStore((s) => s.identities);
  const selectedGroupId = useWorkspaceStore((s) => s.selectedGroupId);
  const storeView = useWorkspaceStore((s) => s.view);
  const selectedSessionId = useWorkspaceStore((s) => s.selectedSessionId);

  const tab = searchParams.get('tab');
  const groupParam = searchParams.get('group');
  const sessionParam = searchParams.get('session');
  const botParam = searchParams.get('bot');
  const membershipParam = searchParams.get('membership');
  // 仅首次挂载时执行一次协作群 session 反查 + 身份切换，避免用户手动切 bot 后
  // URL 残留 session= 导致反复切回用户身份。
  const isFirstUrlSyncRef = useRef(true);

  // 挂载时一次性快照协作群外链参数。外链/邀请直达（session= 且无 bot=）才需要把身份切回用户；
  // 用户后续手动点击协作群产生的 session= 属于内部选中，不应切身份，否则会把手动选中的 bot 身份切回用户。
  const initialGroupUrlRef = useRef({
    session: sessionParam,
    bot: botParam,
    group: groupParam,
    membership: membershipParam,
  });

  // view：以 store.view 为权威（身份切换/记忆恢复都写入 store）。URL 中的 tab=group
  // / group= 仅作为外链直达初始化（见下方 URL → store view effect）。
  const view: WorkspaceView = storeView;

  const ensureGroupExpanded = useCallback((groupId: string) => {
    const store = useWorkspaceStore.getState();
    if (!store.expandedGroupIds[groupId]) store.toggleGroupExpanded(groupId);
  }, []);

  // 首次挂载：把 URL 中的 tab=/group=/session= 初始化到 store.view（支持外链直达）。
  // session= 不带 bot= 时属于协作群会话（如协作广场创建后跳转），也落到 group 视图。
  useEffect(() => {
    const initialView: WorkspaceView =
      tab === 'group' || groupParam !== null || (sessionParam !== null && botParam === null) ? 'group' : 'chat';
    if (useWorkspaceStore.getState().view !== initialView) {
      useWorkspaceStore.getState().setView(initialView);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // URL → Store 单向同步：仅在 URL 参数变化时回填选中态，避免与 Store 内部更新相互反弹。
  useEffect(() => {
    if (groupParam && groupParam !== selectedGroupId) {
      useWorkspaceStore.getState().selectGroup(groupParam);
    } else if (!groupParam && selectedGroupId && tab === 'group') {
      // URL 中无 group 但处于 group 视图，清空选中。
      useWorkspaceStore.getState().selectGroup(null);
    }
    if (groupParam) ensureGroupExpanded(groupParam);
    // session 选中交给会话 Hook 在 Task 10 接管；这里仅做最小回填。
    // 单聊（带有 bot= 参数）不在此处理，走下方单聊回填。
    if (sessionParam && sessionParam !== selectedSessionId && !botParam) {
      useWorkspaceStore.getState().selectSession(sessionParam);
    }
    // 单聊会话：从 URL 的 bot + session 回填选中；bot-only 时保留侧边栏展开态。
    if (tab !== 'group' && botParam) {
      const store = useWorkspaceStore.getState();
      const user = store.identities.find((identity) => identity.kind === 'user');
      if (user && store.activeIdentityId !== user.id) store.setActiveIdentityId(user.id);
      const workspace = useWorkspaceStore.getState();
      workspace.setView('chat');
      if (!workspace.expandedBotIds[botParam]) workspace.toggleBotExpanded(botParam);
      workspace.setBotExpandedSection(botParam, 'mine');
      if (sessionParam && workspace.selectedBotSessionId !== sessionParam) {
        workspace.selectBotSession(sessionParam);
        workspace.bumpHistoryRefresh();
      } else if (!sessionParam && workspace.selectedBotSessionId) {
        workspace.selectBotSession(null);
      }
      return;
    }
    // 协作群会话（协作广场跳转 / 邀请链接）：仅在首次挂载时执行一次，后续身份切换
    // 不应再触发——否则用户手动切到 bot 角色后，URL 残留的 session= 会导致反复切回
    // 用户身份（isFirstUrlSyncRef 守卫，与下方 isFirstIdentityRef 同构）。
    const initialGroupUrl = initialGroupUrlRef.current;
    if (isFirstUrlSyncRef.current && initialGroupUrl.session && !initialGroupUrl.bot) {
      isFirstUrlSyncRef.current = false;
      const store = useWorkspaceStore.getState();
      const me = store.identities.find((i) => i.kind === 'user');
      if (me && store.activeIdentityId !== me.id) store.setActiveIdentityId(me.id);
      const fresh = useWorkspaceStore.getState();
      fresh.setView('group');
      if (initialGroupUrl.membership === 'session_only' || initialGroupUrl.membership === 'direct') {
        useWorkspaceStore.getState().setMembership(initialGroupUrl.membership);
      }
      if (initialGroupUrl.group) {
        fresh.selectGroup(initialGroupUrl.group);
        ensureGroupExpanded(initialGroupUrl.group);
      } else {
        // 无 group 参数（邀请链接）：异步反查 groupId 后选中。
        void sessionService.getSessionDetail(initialGroupUrl.session).then((res) => {
          if (!res.ok) return;
          const s2 = useWorkspaceStore.getState();
          s2.selectGroup(res.data.groupId);
          s2.selectSession(initialGroupUrl.session);
          ensureGroupExpanded(res.data.groupId);
          s2.bumpHistoryRefresh();
        });
        return;
      }
      fresh.selectSession(initialGroupUrl.session);
      fresh.bumpHistoryRefresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ensureGroupExpanded, groupParam, identities, sessionParam, tab, botParam]);

  // 身份切换 → 将 URL 同步到 store 记忆恢复后的状态，避免上一身份残留的 URL 参数
  // 通过上方 URL → store 回填 effect 覆盖刚恢复的选中态（群/会话/bot）。
  const isFirstIdentityRef = useRef(true);
  useEffect(() => {
    if (isFirstIdentityRef.current) {
      isFirstIdentityRef.current = false;
      return;
    }
    const store = useWorkspaceStore.getState();
    const params = new URLSearchParams();
    if (store.view === 'group') {
      params.set('tab', 'group');
      if (store.selectedGroupId) params.set('group', store.selectedGroupId);
      if (store.selectedSessionId) params.set('session', store.selectedSessionId);
    } else {
      params.set('tab', 'chat');
      const expandedBotId = Object.keys(store.expandedBotIds)[0];
      if (expandedBotId && store.selectedBotSessionId) {
        params.set('bot', expandedBotId);
        params.set('session', store.selectedBotSessionId);
      }
    }
    const next = params.toString();
    const current = window.location.search.replace(/^\?/, '');
    if (next !== current) {
      history.replace(`${window.location.pathname}${next ? `?${next}` : ''}${window.location.hash}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdentityId]);

  const setView = useCallback(
    (next: WorkspaceView) => {
      const params = new URLSearchParams(searchParams);
      if (next === 'group') {
        params.set('tab', 'group');
        params.delete('bot');
        params.delete('session');
      } else {
        params.set('tab', 'chat');
        params.delete('group');
        params.delete('session');
      }
      // 同步写入 store，确保 store.view 与 URL tab 保持一致（供身份切换记忆使用）。
      useWorkspaceStore.getState().setView(next);
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  // URL → store view：URL 中的 tab 与 store.view 不一致时回填 store（例如外链直达 / 浏览器后退）。
  useEffect(() => {
    if (view === storeView) return;
    useWorkspaceStore.getState().setView(view);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // Store view → URL：身份切换后 store 的 view（记忆恢复）与 URL 推导不一致时同步 URL。
  useEffect(() => {
    if (storeView === view) return;
    // 外链直达单聊期间不回写 URL,避免 store 记忆的 group 在对齐 chat 时把 ?bot=/?session= 清掉。
    if (tab === 'chat' && botParam && sessionParam) return;
    const params = new URLSearchParams(searchParams);
    if (storeView === 'group') {
      params.set('tab', 'group');
      params.delete('bot');
      params.delete('session');
    } else {
      params.set('tab', 'chat');
      params.delete('group');
      params.delete('session');
    }
    setSearchParams(params, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeView, tab, botParam, sessionParam]);

  // Store → URL 反向同步：选中群/会话变化时把 group=/session= 写回 URL，便于分享/刷新回填。
  // 从零构建 URL，避免从 stale searchParams 继承另一视图的残留参数（如 chat 的 bot=）。
  useEffect(() => {
    if (view !== 'group') return;
    // 外链直达单聊期间不覆盖为 group,保留 ?tab=chat&bot=&session= 由单聊回填处理。
    if (tab === 'chat' && botParam && sessionParam) return;
    const params = new URLSearchParams();
    params.set('tab', 'group');
    if (selectedGroupId) params.set('group', selectedGroupId);
    if (selectedSessionId) params.set('session', selectedSessionId);
    const next = params.toString();
    const current = new URLSearchParams(window.location.search).toString();
    if (next !== current) {
      const path = `${window.location.pathname}${next ? `?${next}` : ''}${window.location.hash}`;
      history.replace(path);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedGroupId, selectedSessionId, view, tab, botParam, sessionParam]);

  return useMemo(
    () => ({ view, setView, activeIdentityId, identities }),
    [view, setView, activeIdentityId, identities],
  );
}
