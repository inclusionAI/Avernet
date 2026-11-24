import { COMMUNITY_REPLY_PAGE_SIZE } from '@/domain/community/constants';
import { communityErrorMessage } from '@/domain/community/errors';
import type { CommunityReply, CommunityReplyPage } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCallback, useRef, useState } from 'react';

interface ReplyWindow {
  firstReplyPage: number;
  lastReplyPage: number;
  replyTotal: number;
  replyLoading: boolean;
  replyError: string | null;
  failedReplyPage: number | null;
}

const initialWindow: ReplyWindow = {
  firstReplyPage: 1,
  lastReplyPage: 1,
  replyTotal: 0,
  replyLoading: false,
  replyError: null,
  failedReplyPage: null,
};

export function useCommunityReplies(setReplies: (replies: CommunityReply[]) => void) {
  const [window, setWindow] = useState(initialWindow);
  const pages = useRef(new Map<number, CommunityReply[]>());
  const request = useRef({ generation: 0, inFlightPage: null as number | null });

  const resetReplies = useCallback(() => {
    request.current = { generation: request.current.generation + 1, inFlightPage: null };
    pages.current.clear();
    setReplies([]);
    setWindow(initialWindow);
  }, [setReplies]);

  const fetchReplyPage = useCallback(async (topicId: string, page: number): Promise<CommunityReplyPage | null> => {
    if (request.current.inFlightPage === page) return null;
    const generation = request.current.generation + 1;
    request.current = { generation, inFlightPage: page };
    setWindow((previous) => ({
      ...previous,
      ...(pages.current.size === 0 ? { firstReplyPage: page, lastReplyPage: page } : {}),
      replyLoading: true,
      replyError: null,
      failedReplyPage: null,
    }));
    try {
      const result = await communityService.listReplies(topicId, { page, pageSize: COMMUNITY_REPLY_PAGE_SIZE });
      if (request.current.generation !== generation) return null;
      pages.current.set(page, result.items);
      const pageNumbers = [...pages.current.keys()].sort((a, b) => a - b);
      const uniqueReplies = new Map<string, CommunityReply>();
      for (const number of pageNumbers) {
        for (const reply of pages.current.get(number) ?? []) uniqueReplies.set(reply.id, reply);
      }
      setReplies([...uniqueReplies.values()]);
      setWindow({
        firstReplyPage: pageNumbers[0],
        lastReplyPage: pageNumbers[pageNumbers.length - 1],
        replyTotal: result.total,
        replyLoading: false,
        replyError: null,
        failedReplyPage: null,
      });
      return result;
    } catch (error) {
      if (request.current.generation === generation) {
        setWindow((previous) => ({
          ...previous,
          replyLoading: false,
          failedReplyPage: page,
          replyError: communityErrorMessage(error, '回复加载失败'),
        }));
      }
      return null;
    } finally {
      if (request.current.generation === generation) request.current.inFlightPage = null;
    }
  }, [setReplies]);

  const findReplyPage = useCallback((postId: string) => {
    for (const [page, replies] of pages.current) {
      if (replies.some((reply) => reply.id === postId || reply.childReplies?.some((child) => child.id === postId))) {
        return page;
      }
    }
    return undefined;
  }, []);

  return {
    ...window,
    replyPageCount: Math.max(1, Math.ceil(window.replyTotal / COMMUNITY_REPLY_PAGE_SIZE)),
    resetReplies,
    fetchReplyPage,
    findReplyPage,
  };
}
