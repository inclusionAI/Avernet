import { buildTopicListSummaryQueries } from '@/domain/community/interactionQueries';
import type {
  CommunityTopic,
  InteractionSummary,
  PersonalCenterActivity,
} from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useEffect, useRef, useState } from 'react';

export interface FollowedAuthorTopics {
  topics: CommunityTopic[];
  summariesById: Record<string, InteractionSummary>;
  loading: boolean;
}

/** Load the selected followed identity's topics and their aggregate interaction stats. */
export function useFollowedAuthorTopics(
  selectedItem: PersonalCenterActivity | undefined,
): FollowedAuthorTopics {
  const [topics, setTopics] = useState<CommunityTopic[]>([]);
  const [summariesById, setSummariesById] = useState<Record<string, InteractionSummary>>({});
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(0);

  useEffect(() => {
    if (!selectedItem) {
      setTopics([]);
      setSummariesById({});
      return;
    }
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setLoading(true);
    communityService
      .listTopics({ authorId: selectedItem.target.id, limit: 20 })
      .then((page) => {
        if (requestId !== requestRef.current) return;
        setTopics(page.items);
        setSummariesById({});
        if (!page.items.length) return;

        void communityService
          .getInteractionSummaryBatch(buildTopicListSummaryQueries(page.items))
          .then((summaries) => {
            if (requestId !== requestRef.current) return;
            const nextSummaries: Record<string, InteractionSummary> = {};
            summaries.forEach((summary) => {
              const target = summary.target;
              if (target?.targetType === 'TOPIC') nextSummaries[target.id] = summary;
            });
            setSummariesById(nextSummaries);
          })
          .catch(() => {
            if (requestId !== requestRef.current) return;
            setSummariesById({});
          });
      })
      .catch(() => {
        if (requestId !== requestRef.current) return;
        setTopics([]);
        setSummariesById({});
      })
      .finally(() => {
        if (requestId === requestRef.current) setLoading(false);
      });
  }, [requestRef, selectedItem]);

  return { topics, summariesById, loading };
}
