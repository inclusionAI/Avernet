import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useRef } from 'react';
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
  const openTopicByIdRef = useRef(openTopicById);

  // Loading a topic updates interaction/pending state. That recursively changes
  // the callback identity in useCommunity, so the callback itself must not be a
  // deep-link dependency. Keeping the latest callback in a ref opens the URL
  // target once while allowing the effect below to depend only on target keys.
  useEffect(() => {
    openTopicByIdRef.current = openTopicById;
  }, [openTopicById]);

  useEffect(
    () => {
      if (!notificationTopicId) return;
      setPublishOpen(false);
      setEditOpen(false);
      setShowPersonalCenter(false);
      void openTopicByIdRef.current?.({
        topicId: notificationTopicId,
        postId: notificationPostId || undefined,
      });
    },
    [
      notificationPostId,
      notificationTopicId,
      setEditOpen,
      setPublishOpen,
      setShowPersonalCenter,
    ],
  );
}
