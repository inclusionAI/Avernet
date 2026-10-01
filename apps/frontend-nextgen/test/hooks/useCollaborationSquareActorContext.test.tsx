/** @jest-environment jsdom */
import { useCollaborationSquareActorContext } from '@/hooks/useCollaborationSquareActorContext';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;

describe('useCollaborationSquareActorContext', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useWorkspaceStore.getState().reset();
    useSquareIdentityStore.getState().reset();
    useWorkspaceStore.getState().setIdentities(
      [
        { id: 'human_900003', kind: 'user', displayName: '当前用户', online: true },
        { id: 'bot-1:900003', kind: 'bot', displayName: '当前 Bot', online: true },
      ],
      'human_900003',
    );
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '900003', displayName: '当前用户', online: true },
      status: 'ready',
    });
  });

  it('公开Bot Tab 未选择身份时以登录用户身份生成 viewer，身份切换时重置广场状态', () => {
    const reset = jest.fn();
    const { result } = renderHook(() => useCollaborationSquareActorContext('bot', reset));

    expect(result.current).toMatchObject({
      humanIdentityStatus: 'ready',
      humanBotContext: { actorId: 'human_900003', userId: '900003' },
      viewer: { viewerActorType: 'human', viewerActorId: '900003' },
      activeActor: { type: 'human', id: '900003' },
    });
    expect(reset).not.toHaveBeenCalled();

    act(() => useSquareIdentityStore.getState().selectIdentity('bot-1:900003'));

    expect(result.current).toMatchObject({
      humanBotContext: { actorId: 'bot-1:900003', userId: '900003' },
      viewer: { viewerActorType: 'bot', viewerActorId: 'bot-1:900003' },
      activeActor: { type: 'bot', id: 'bot-1:900003' },
    });
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it.each(['group', 'task'] as const)('%s Tab 固定登录用户身份，模块级 Bot 选择不生效', (resource) => {
    act(() => useSquareIdentityStore.getState().selectIdentity('bot-1:900003'));

    const reset = jest.fn();
    const { result } = renderHook(() => useCollaborationSquareActorContext(resource, reset));

    expect(result.current).toMatchObject({
      viewer: { viewerActorType: 'human', viewerActorId: '900003' },
      activeActor: { type: 'human', id: '900003' },
      humanBotContext: { actorId: 'human_900003', userId: '900003' },
    });
    expect(reset).not.toHaveBeenCalled();
  });

  it('全局工作身份切换不影响任何 Tab 的 viewer（发现菜单与全局身份脱钩）', () => {
    const botResult = renderHook(() => useCollaborationSquareActorContext('bot', jest.fn()));
    const groupResult = renderHook(() => useCollaborationSquareActorContext('group', jest.fn()));

    act(() => useWorkspaceStore.getState().setActiveIdentity('bot-1:900003'));

    expect(botResult.result.current.viewer).toMatchObject({ viewerActorType: 'human', viewerActorId: '900003' });
    expect(groupResult.result.current.viewer).toMatchObject({ viewerActorType: 'human', viewerActorId: '900003' });
  });
});
