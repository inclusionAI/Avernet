import { applyIdentityLoadResult } from '@/services/workspace/identityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { describe, expect, it } from '@jest/globals';

describe('applyIdentityLoadResult', () => {
  it('keeps the active identity when it still exists and avoids writing an unchanged list', () => {
    useWorkspaceStore.setState({
      identities: [{ id: 'human-1', kind: 'user', displayName: '我', online: true }],
      activeIdentityId: 'human-1',
    });
    applyIdentityLoadResult({
      identities: [{ id: 'human-1', kind: 'user', displayName: '我', online: true }],
      defaultActiveId: 'human-1',
    });

    expect(useWorkspaceStore.getState().activeIdentityId).toBe('human-1');
    expect(useWorkspaceStore.getState().identities).toHaveLength(1);
  });

  it('falls back when the active identity is no longer present', () => {
    useWorkspaceStore.setState({
      identities: [{ id: 'bot-old', kind: 'bot', displayName: 'Old Bot', online: true }],
      activeIdentityId: 'bot-old',
    });
    applyIdentityLoadResult({
      identities: [{ id: 'human-1', kind: 'user', displayName: '我', online: true }],
      defaultActiveId: 'human-1',
    });

    expect(useWorkspaceStore.getState().activeIdentityId).toBe('human-1');
  });

  it('falls back to the default identity when none is active', () => {
    useWorkspaceStore.setState({ identities: [], activeIdentityId: null });
    applyIdentityLoadResult({
      identities: [
        { id: 'human-1', kind: 'user', displayName: '我', online: true },
        { id: 'bot-1', kind: 'bot', displayName: 'Bot', online: true },
      ],
      defaultActiveId: 'bot-1',
    });

    expect(useWorkspaceStore.getState().activeIdentityId).toBe('bot-1');
  });
});
