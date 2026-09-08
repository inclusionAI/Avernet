/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { useSelectedGroupDetail } from '@/pages/Workspace/hooks/useSelectedGroupDetail';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

const g9Group: GroupView = {
  groupId: 'g9',
  name: 'G9',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 1,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

beforeEach(() => {
  useWorkspaceStore.getState().resetWorkspace();
});

describe('useSelectedGroupDetail', () => {
  it('列表已为当前身份加载完成且漏选时,把 direct 纠正到 session_only', async () => {
    useWorkspaceStore.setState({ activeIdentityId: 'me', membership: 'direct' });
    const list: GroupView[] = []; // 人类 direct 列表里没有 g9
    renderHook(() => useSelectedGroupDetail('g9', list, false, true));
    await waitFor(() => expect(useWorkspaceStore.getState().membership).toBe('session_only'));
  });

  it('新开页挂载竞态:列表尚未为当前身份加载完成时,不据初始空列表错切 membership', () => {
    useWorkspaceStore.setState({ activeIdentityId: 'me', membership: 'direct' });
    const before = useWorkspaceStore.getState().membership;
    // selectedGroupId 已写入深链群,但人类 direct 列表尚未加载完成(listReady=false)。
    renderHook(() => useSelectedGroupDetail('g9', [], false, false));
    // 不应按初始空列表漏选把 direct 错切到 session_only。
    expect(useWorkspaceStore.getState().membership).toBe(before);
    expect(useWorkspaceStore.getState().membership).toBe('direct');
  });

  it('人类 direct 列表加载完成且含深链群:无需切视角,保持 direct', () => {
    useWorkspaceStore.setState({ activeIdentityId: 'me', membership: 'direct' });
    renderHook(() => useSelectedGroupDetail('g9', [g9Group], false, true));
    expect(useWorkspaceStore.getState().membership).toBe('direct');
  });
});
