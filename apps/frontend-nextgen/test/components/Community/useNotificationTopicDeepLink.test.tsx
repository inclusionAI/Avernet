/** @jest-environment jsdom */
import { useNotificationTopicDeepLink } from '@/components/Community/CommunityPage/useNotificationTopicDeepLink';
import { renderHook } from '@testing-library/react';

jest.mock('@umijs/max', () => ({
  useSearchParams: () => [
    new URLSearchParams('topicId=topic-1&postId=post-1'),
  ],
}));

describe('useNotificationTopicDeepLink', () => {
  it('opens a URL target once even while the callback identity changes', () => {
    const firstOpen = jest.fn();
    const secondOpen = jest.fn();
    const setters = {
      setPublishOpen: jest.fn(),
      setEditOpen: jest.fn(),
      setShowPersonalCenter: jest.fn(),
    };

    const { rerender } = renderHook(
      ({ openTopicById }: { openTopicById: () => void }) =>
        useNotificationTopicDeepLink({
          openTopicById,
          ...setters,
        }),
      { initialProps: { openTopicById: firstOpen } },
    );

    expect(firstOpen).toHaveBeenCalledTimes(1);
    expect(firstOpen).toHaveBeenCalledWith({
      topicId: 'topic-1',
      postId: 'post-1',
    });
    expect(setters.setPublishOpen).toHaveBeenCalledWith(false);
    expect(setters.setEditOpen).toHaveBeenCalledWith(false);
    expect(setters.setShowPersonalCenter).toHaveBeenCalledWith(false);

    rerender({ openTopicById: secondOpen });

    expect(firstOpen).toHaveBeenCalledTimes(1);
    expect(secondOpen).not.toHaveBeenCalled();
  });
});
