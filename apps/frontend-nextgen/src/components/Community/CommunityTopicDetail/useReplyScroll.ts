import type { CommunityPostLocation, CommunityReply } from '@/domain/community/types';
import { useCallback, useEffect, useLayoutEffect, useRef, type UIEvent, type WheelEvent } from 'react';

interface ReplyScrollOptions {
  firstPostId?: string;
  firstReplyPage: number;
  lastReplyPage: number;
  replyPageCount: number;
  onGoToReplyPage: (page: number) => Promise<void>;
  replies: CommunityReply[];
  expandedReplyIds: Record<string, boolean>;
  loading: boolean;
  pendingPostLocation: CommunityPostLocation | null;
  onClearPendingPostLocation?: () => void;
  edgeLoadingBlocked: boolean;
}

export function useReplyScroll({
  firstPostId, firstReplyPage, lastReplyPage, replyPageCount, onGoToReplyPage,
  replies, expandedReplyIds, loading, pendingPostLocation, onClearPendingPostLocation,
  edgeLoadingBlocked,
}: ReplyScrollOptions) {
  const scrollRoot = useRef<HTMLElement>(null);
  const scrollPosition = useRef(0);
  const replyElementsRef = useRef(new Map<string, HTMLElement | null>());
  const registerReplyElement = useCallback(
    (postId?: string) => (element: HTMLElement | null) => {
      if (postId) replyElementsRef.current.set(postId, element);
    },
    [],
  );

  const locateTargetReply = useCallback((postId: string, behavior: ScrollBehavior = 'smooth') => {
    const element = replyElementsRef.current.get(postId);
    if (!element) return;
    element.scrollIntoView({ block: 'center', behavior });
    if (behavior === 'instant' && scrollRoot.current) scrollPosition.current = scrollRoot.current.scrollTop;
    element.classList.add('bg-primary/10');
    const clearOnNextClick = () => element.classList.remove('bg-primary/10');
    window.setTimeout(() => window.addEventListener('click', clearOnNextClick, { once: true }), 0);
  }, []);

  useEffect(() => {
    if (!pendingPostLocation || loading) return;
    const element = replyElementsRef.current.get(pendingPostLocation.postId);
    if (!element) return;
    locateTargetReply(pendingPostLocation.postId, 'instant');
    onClearPendingPostLocation?.();
  }, [expandedReplyIds, loading, locateTargetReply, onClearPendingPostLocation, pendingPostLocation, replies]);

  const requestedPage = useRef<number | null>(null);
  const prependAnchor = useRef<{ page: number; postId: string; top: number } | null>(null);
  const loadReplyPage = (page: number) => {
    if (requestedPage.current !== null) return;
    requestedPage.current = page;
    if (page < firstReplyPage) {
      const postId = firstPostId;
      const element = postId ? replyElementsRef.current.get(postId) : null;
      if (postId && element) prependAnchor.current = { page, postId, top: element.getBoundingClientRect().top };
    }
    void onGoToReplyPage(page).finally(() => {
      if (requestedPage.current === page) requestedPage.current = null;
    });
  };

  useLayoutEffect(() => {
    const anchor = prependAnchor.current;
    const root = scrollRoot.current;
    if (!anchor || !root || anchor.page !== firstReplyPage) return;
    prependAnchor.current = null;
    const element = replyElementsRef.current.get(anchor.postId);
    if (!element) return;
    root.scrollTop += element.getBoundingClientRect().top - anchor.top;
    scrollPosition.current = root.scrollTop;
  }, [firstReplyPage, replies]);

  const loadNearEdge = (root: HTMLElement, direction: number) => {
    if (edgeLoadingBlocked) return;
    if (direction < 0 && root.scrollTop <= 80 && firstReplyPage > 1) {
      loadReplyPage(firstReplyPage - 1);
    } else if (direction > 0 && root.scrollHeight - root.scrollTop - root.clientHeight <= 80 && lastReplyPage < replyPageCount) {
      loadReplyPage(lastReplyPage + 1);
    }
  };
  const onScroll = ({ currentTarget: root }: UIEvent<HTMLElement>) => {
    const direction = root.scrollTop - scrollPosition.current;
    scrollPosition.current = root.scrollTop;
    loadNearEdge(root, direction);
  };
  const onWheel = ({ currentTarget, deltaY }: WheelEvent<HTMLElement>) => loadNearEdge(currentTarget, deltaY);

  return { scrollRoot, registerReplyElement, locateTargetReply, loadReplyPage, onScroll, onWheel };
}
