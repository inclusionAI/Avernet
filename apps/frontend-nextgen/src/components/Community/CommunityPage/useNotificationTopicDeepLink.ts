import type { Dispatch, SetStateAction } from 'react';
import { useEffect } from 'react';
import { useSearchParams } from '@umijs/max';

interface NotificationTopicDeepLinkProps {
  openTopicById?: (target: { topicId: string; postId?: string }) => Promise<void> | void;
  setPublishOpen: Dispatch<SetStateAction<boolean>>;
  setEditOpen: Dispatch<SetStateAction<boolean>>;
  setShowPersonalCenter: Dispatch<SetStateAction<boolean>>;
}

/** Open a BBS notification's topic, including exact floor positioning. */
export function useNotificationTopicDeepLink({
  openTopicById,
  setPublishOpen,
  setEditOpen,
  setShowPersonalCenter,
}: NotificationTopicDeepLinkProps) {
  const [searchParams] = useSearchParams();
  const notificationTopicId = searchParams.get('topicId') ?? '';
  const notificationPostId = searchParams.get('postId') ?? '';

  useEffect(
    () => {
      if (!notificationTopicId) return;
      setPublishOpen(false);
      setEditOpen(false);
      setShowPersonalCenter(false);
      void openTopicById?.({
        topicId: notificationTopicId,
        postId: notificationPostId || undefined,
      });
    },
    [
      openTopicById,
      notificationPostId,
      notificationTopicId,
      setEditOpen,
      setPublishOpen,
      setShowPersonalCenter,
    ],
  );
}
