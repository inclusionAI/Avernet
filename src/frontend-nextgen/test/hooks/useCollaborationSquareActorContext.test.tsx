/** @jest-environment jsdom */
import { useCollaborationSquareActorContext } from '@/hooks/useCollaborationSquareActorContext';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;

describe('useCollaborationSquareActorContext', () => {
  beforeEach(() => {
    useWorkspaceStore.getState().reset();
    useWorkspaceStore.getState().setIdentities(
      [
        { id: 'human_327325', kind: 'user', displayName: '当前用户', online: true },
        { id: 'bot-1:327325', kind: 'bot', displayName: '当前 Bot', online: true },
      ],
      'human_327325',
    );
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '327325', displayName: '当前用户', online: true },
      status: 'ready',
    });
  });

  it('按当前工作身份生成操作上下文和目录 viewer，身份切换时重置广场状态', () => {
    const reset = jest.fn();
    const { result } = renderHook(() => useCollaborationSquareActorContext(reset));

    expect(result.current).toMatchObject({
      humanIdentityStatus: 'ready',
      humanBotContext: { actorId: 'human_327325', userId: '327325' },
      viewer: { viewerActorType: 'human', viewerActorId: '327325' },
      activeActor: { type: 'human', id: '327325' },
    });
    expect(reset).not.toHaveBeenCalled();

    act(() => useWorkspaceStore.getState().setActiveIdentity('bot-1:327325'));

    expect(result.current).toMatchObject({
      humanIdentityStatus: 'ready',
      humanBotContext: { actorId: 'bot-1:327325', userId: '327325' },
      viewer: { viewerActorType: 'bot', viewerActorId: 'bot-1:327325' },
      activeActor: { type: 'bot', id: 'bot-1:327325' },
    });
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
