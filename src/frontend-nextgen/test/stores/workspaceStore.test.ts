import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

describe('workspaceStore', () => {
  beforeEach(() => useWorkspaceStore.getState().resetWorkspace());

  it('setActiveIdentity 记忆并恢复群/会话选中态', () => {
    const s = useWorkspaceStore.getState();
    s.setIdentities(
      [
        { id: 'me', kind: 'user', displayName: '我', online: true },
        { id: 'b1', kind: 'bot', displayName: 'B1', online: true },
      ],
      'me',
    );
    // 用户身份下选中群 g1、会话 s1、展开群 g1
    s.setView('group');
    s.toggleGroupExpanded('g1');
    s.selectGroup('g1');
    s.selectSession('s1');
    // 切换到 bot b1（仅 group 视图）
    s.setActiveIdentity('b1');
    // b1 无记忆，选中态清空
    expect(useWorkspaceStore.getState().selectedGroupId).toBeNull();
    expect(useWorkspaceStore.getState().selectedSessionId).toBeNull();
    // 切换回用户 me
    s.setActiveIdentity('me');
    const st = useWorkspaceStore.getState();
    expect(st.view).toBe('group');
    expect(st.selectedGroupId).toBe('g1');
    expect(st.selectedSessionId).toBe('s1');
    expect(st.expandedGroupIds).toEqual({ g1: true });
  });

  it('setActiveIdentity 记忆并恢复单聊 bot/会话选中态', () => {
    const s = useWorkspaceStore.getState();
    s.setIdentities(
      [
        { id: 'me', kind: 'user', displayName: '我', online: true },
        { id: 'b1', kind: 'bot', displayName: 'B1', online: true },
      ],
      'me',
    );
    // 用户身份下展开 bot b1、选中会话 sBot1
    s.setView('chat');
    s.toggleBotExpanded('b1');
    s.selectBotSession('sBot1');
    // 切换到 bot b1
    s.setActiveIdentity('b1');
    // 切换回用户 me
    s.setActiveIdentity('me');
    const st = useWorkspaceStore.getState();
    expect(st.view).toBe('chat');
    expect(st.selectedBotSessionId).toBe('sBot1');
    expect(st.expandedBotIds).toEqual({ b1: true });
  });

  it('setActiveIdentity 记忆并恢复 membership 视角(群成员/会话成员)', () => {
    const s = useWorkspaceStore.getState();
    s.setIdentities(
      [
        { id: 'me', kind: 'user', displayName: '我', online: true },
        { id: 'b1', kind: 'bot', displayName: 'B1', online: true },
      ],
      'me',
    );
    // 用户身份下选择「会话成员」视角并选中群+会话
    s.setView('group');
    s.setMembership('session_only');
    s.toggleGroupExpanded('g1');
    s.selectGroup('g1');
    s.selectSession('s1');
    // 切到 bot 再切回
    s.setActiveIdentity('b1');
    s.setActiveIdentity('me');
    const st = useWorkspaceStore.getState();
    expect(st.membership).toBe('session_only');
    expect(st.selectedGroupId).toBe('g1');
    expect(st.selectedSessionId).toBe('s1');
    expect(st.expandedGroupIds).toEqual({ g1: true });
  });

  it('setActiveIdentity resets selection', () => {
    const s = useWorkspaceStore.getState();
    s.setIdentities([{ id: 'bot-1', kind: 'bot', displayName: 'B1', online: true }], 'bot-1');
    s.selectGroup('g1');
    s.selectSession('s1');
    s.setActiveIdentity('bot-2');
    const st = useWorkspaceStore.getState();
    expect(st.selectedGroupId).toBeNull();
    expect(st.selectedSessionId).toBeNull();
    expect(st.sessionTabsByGroup).toEqual({});
    expect(st.sessionSearchText).toBe('');
    expect(st.activeIdentityId).toBe('bot-2');
  });

  it('selectGroup clears session selection but keeps expand state', () => {
    const s = useWorkspaceStore.getState();
    s.toggleGroupExpanded('g1');
    s.selectSession('s1');
    s.selectGroup('g2');
    expect(useWorkspaceStore.getState().selectedSessionId).toBeNull();
    expect(useWorkspaceStore.getState().expandedGroupIds['g1']).toBe(true);
    expect(useWorkspaceStore.getState().selectedGroupId).toBe('g2');
  });

  it('setConnectionState/state transitions are sync only', () => {
    const s = useWorkspaceStore.getState();
    s.setConnectionState('reconnecting');
    expect(useWorkspaceStore.getState().connectionState).toBe('reconnecting');
    s.setConnectionState('connected');
    expect(useWorkspaceStore.getState().connectionState).toBe('connected');
  });

  it('membership defaults to direct and setMembership switches to session_only', () => {
    const s = useWorkspaceStore.getState();
    expect(s.membership).toBe('direct');
    s.setMembership('session_only');
    expect(useWorkspaceStore.getState().membership).toBe('session_only');
  });

  it('resetWorkspace restores membership to direct', () => {
    const s = useWorkspaceStore.getState();
    s.setMembership('session_only');
    useWorkspaceStore.getState().resetWorkspace();
    expect(useWorkspaceStore.getState().membership).toBe('direct');
  });

  it('setActiveIdentity 对 bot 身份钳制 view 为 group', () => {
    const s = useWorkspaceStore.getState();
    s.setIdentities(
      [
        { id: 'me', kind: 'user', displayName: '我', online: true },
        { id: 'b1', kind: 'bot', displayName: 'B1', online: true },
      ],
      'me',
    );
    s.setView('chat');
    s.setActiveIdentity('b1');
    expect(useWorkspaceStore.getState().view).toBe('group');
  });

  it('setActiveIdentity 对 test-user 钳制 view 为 chat', () => {
    const s = useWorkspaceStore.getState();
    s.setIdentities([{ id: 'test-user', kind: 'user', displayName: '测试用户', online: true }], 'test-user');
    s.setView('group');
    s.setActiveIdentity('test-user');
    expect(useWorkspaceStore.getState().view).toBe('chat');
  });

  it('toggleBotExpanded / selectBotSession 独立于群展开态', () => {
    const s = useWorkspaceStore.getState();
    s.toggleBotExpanded('bot-1');
    expect(useWorkspaceStore.getState().selectedBotSessionId).toBeNull();
    s.selectBotSession('sid-1');
    expect(useWorkspaceStore.getState().expandedBotIds['bot-1']).toBe(true);
    expect(useWorkspaceStore.getState().selectedBotSessionId).toBe('sid-1');
  });

  it('toggleGroupExpanded 手风琴互斥:展开新群时收起旧群,再次点击则全部收起', () => {
    const s = useWorkspaceStore.getState();
    s.toggleGroupExpanded('g1');
    expect(useWorkspaceStore.getState().expandedGroupIds).toEqual({ g1: true });
    s.toggleGroupExpanded('g2');
    expect(useWorkspaceStore.getState().expandedGroupIds).toEqual({ g2: true });
    s.toggleGroupExpanded('g2');
    expect(useWorkspaceStore.getState().expandedGroupIds).toEqual({});
  });

  it('toggleBotExpanded 手风琴互斥:展开新 bot 时收起旧 bot', () => {
    const s = useWorkspaceStore.getState();
    s.toggleBotExpanded('b1');
    s.toggleBotExpanded('b2');
    expect(useWorkspaceStore.getState().expandedBotIds).toEqual({ b2: true });
  });

  it('bumpHistoryRefresh 递增 nonce(点击会话驱动历史重载)', () => {
    const before = useWorkspaceStore.getState().historyRefreshNonce;
    useWorkspaceStore.getState().bumpHistoryRefresh();
    expect(useWorkspaceStore.getState().historyRefreshNonce).toBe(before + 1);
    useWorkspaceStore.getState().bumpHistoryRefresh();
    expect(useWorkspaceStore.getState().historyRefreshNonce).toBe(before + 2);
  });

  it('setSessionTabForGroup sets per-group tab immutably and resetWorkspace clears', () => {
    const s = useWorkspaceStore.getState();
    expect(s.sessionTabsByGroup).toEqual({});
    s.setSessionTabForGroup('g1', 'favorite');
    expect(useWorkspaceStore.getState().sessionTabsByGroup).toEqual({ g1: 'favorite' });
    // 设置第二个群不影响第一个
    s.setSessionTabForGroup('g2', 'favorite');
    expect(useWorkspaceStore.getState().sessionTabsByGroup).toEqual({ g1: 'favorite', g2: 'favorite' });
    // 切换回 all 仅影响该群
    s.setSessionTabForGroup('g1', 'all');
    expect(useWorkspaceStore.getState().sessionTabsByGroup).toEqual({ g1: 'all', g2: 'favorite' });
    useWorkspaceStore.getState().resetWorkspace();
    expect(useWorkspaceStore.getState().sessionTabsByGroup).toEqual({});
  });
});
