/** @jest-environment jsdom */
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { notifyError } from '@/components/ui/notify';
import type { PersonalCenterActivity } from '@/domain/community/types';
import { usePersonalCenter } from '@/hooks/usePersonalCenter';
import { communityService } from '@/services/community';
import '@testing-library/jest-dom';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn() }));
jest.mock('@/services/community', () => ({
  communityService: {
    createInteraction: jest.fn(),
    getPersonalCenterActivities: jest.fn(),
  },
}));

type LoadedActivity = Awaited<ReturnType<typeof communityService.getPersonalCenterActivities>>;

const mockedIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;

function activity(targetType: PersonalCenterActivity['target']['targetType'], id: string): PersonalCenterActivity {
  return {
    activityType: 'FOLLOWED',
    actor: { actorType: 'HUMAN', id: 'owner-id' },
    target: { targetType, id },
    happenedAt: '2026-10-18T08:00:00Z',
    bodyPreview: `activity-${id}`,
  };
}

function group(targetType: PersonalCenterActivity['target']['targetType'], id: string) {
  return activity(targetType, id);
}

describe('usePersonalCenter branches', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedIdentity.mockReturnValue({
      identity: {
        userId: 'owner-id',
        displayName: '当前用户',
        online: true,
      },
      status: 'ready',
    } as ReturnType<typeof useHumanIdentity>);
    jest.mocked(communityService.getPersonalCenterActivities).mockResolvedValue({
      items: [],
      total: 0,
    });
    jest.mocked(communityService.createInteraction).mockResolvedValue(undefined);
  });

  it('ignores a stale activity load when the query changes before it settles', async () => {
    let resolveFirst: ((value: LoadedActivity) => void) | undefined;
    const firstQuery = new Promise<LoadedActivity>((resolve) => {
      resolveFirst = resolve;
    });
    const secondQuery = new Promise<LoadedActivity>(() => {});
    jest.mocked(communityService.getPersonalCenterActivities)
      .mockImplementationOnce(() => firstQuery)
      .mockImplementationOnce(() => secondQuery);
    const { result } = renderHook(() => usePersonalCenter());
    await act(async () => {});
    act(() => result.current.goToPage(2));
    await act(async () => {});
    await act(async () => { resolveFirst?.({ items: [group('BOT', 'bot-stale')], total: 1 }); });
    expect(result.current.activityPage.items).toEqual([]);
  });

  it('ignores a stale activity failure when the query changes before it settles', async () => {
    let rejectFirst: ((reason?: unknown) => void) | undefined;
    const firstQuery = new Promise<LoadedActivity>((_, reject) => {
      rejectFirst = reject;
    });
    const secondQuery = new Promise<LoadedActivity>(() => {});
    jest.mocked(communityService.getPersonalCenterActivities)
      .mockImplementationOnce(() => firstQuery)
      .mockImplementationOnce(() => secondQuery);
    const { result } = renderHook(() => usePersonalCenter());
    await act(async () => {});
    act(() => result.current.goToPage(2));
    await act(async () => {});
    await act(async () => { rejectFirst?.(new Error('stale load failed')); });
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it('reports an unfollow failure when the interaction rejects', async () => {
    jest.mocked(communityService.createInteraction).mockRejectedValueOnce(
      new Error('unfollow failed'),
    );
    const { result } = renderHook(() => usePersonalCenter());
    await act(async () => {});
    await act(async () => {
      await result.current.unfollow(group('BOT', 'bot-failed-first'));
    });
    expect(notifyError).toHaveBeenCalledWith('unfollow failed', {
      title: '互动失败',
    });
  });

  it('does not load and reports identity mismatch on unfollow when the owner is absent', async () => {
    mockedIdentity.mockReturnValue({ identity: null, status: 'error' } as ReturnType<typeof useHumanIdentity>);
    const { result } = renderHook(() => usePersonalCenter());
    await act(async () => {});
    expect(communityService.getPersonalCenterActivities).not.toHaveBeenCalled();
    await act(async () => { await result.current.unfollow(group('BOT', 'bot-followed'));  });
    expect(communityService.createInteraction).not.toHaveBeenCalled();
    expect(notifyError).toHaveBeenCalledWith('缺少当前用户身份，无法取消关注', {
      title: '互动失败',
    });
  });

  it('removes the unfollowed identity and reports unfollow failures', async () => {
    const followed = group('BOT', 'bot-followed');
    const keptBot = group('BOT', 'bot-kept');
    const keptTopic = group('TOPIC', 'topic-kept');
    jest.mocked(communityService.getPersonalCenterActivities).mockResolvedValueOnce({
      items: [followed, keptBot, keptTopic],
      total: 3,
    });
    const { result } = renderHook(() => usePersonalCenter());
    await waitFor(() => expect(result.current.activityPage.items).toHaveLength(3));
    jest.mocked(communityService.getPersonalCenterActivities).mockImplementationOnce(
      () => new Promise<LoadedActivity>(() => {}),
    );
    await act(async () => {
      await result.current.unfollow(followed);
    });

    expect(result.current.activityPage.items).toEqual([keptBot, keptTopic]);
    expect(result.current.page).toBe(1);
    expect(communityService.createInteraction).toHaveBeenCalledWith({
      actor: { actorType: 'HUMAN', id: 'owner-id' },
      actionType: 'FOLLOW',
      target: followed.target,
      active: -1,
    });

    const rejectedBefore = result.current.activityPage.items.length;
    jest.mocked(communityService.createInteraction).mockRejectedValueOnce(new Error('unfollow failed'));
    await act(async () => {
      await result.current.unfollow(followed);
    });
    expect(result.current.activityPage.items).toHaveLength(rejectedBefore);
    expect(notifyError).toHaveBeenCalledWith('unfollow failed', {
      title: '互动失败',
    });
  });

  it('keeps an authored activity at its current page when unfollow follows another page', async () => {
    jest.mocked(communityService.createInteraction).mockResolvedValueOnce(undefined);
    const { result } = renderHook(() => usePersonalCenter());
    act(() => result.current.setActivityType('FOLLOWED'));
    act(() => result.current.goToPage(4));
    await act(async () => {});
    await act(async () => {
      await result.current.unfollow(group('BOT', 'bot-failed-page'));
    });
    expect(result.current.page).toBe(1);
  });
});
