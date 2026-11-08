import {
  mapInteractionSummaryQuery,
  type InteractionSummaryDto,
  type InteractionSummaryQueryDto,
} from '@/domain/community/mapper';
import type {
  InteractionSummaryQuery,
  RecordInteractionInput,
} from '@/domain/community/types';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type CommunityInteractionEnvelope<T> = BackendApiEnvelope<T>;

export interface InteractionRecordedDto {
  actor_type?: string;
  actor_id?: string;
  action_type?: string;
  target_type?: string;
  target_id?: string;
  recorded?: boolean;
}

export interface InteractionSummaryBatchRequestDto {
  items: InteractionSummaryQueryDto[];
}

export interface InteractionSummaryBatchResponseDto {
  items: InteractionSummaryDto[];
}

const BBS_INTERACTION_API_BASE = `${TASK_READ_API_BASE}/bbs/interactions`;

export function createCommunityInteraction(input: RecordInteractionInput, signal?: AbortSignal) {
  return backendRequest<CommunityInteractionEnvelope<InteractionRecordedDto>>(
    BBS_INTERACTION_API_BASE,
    {
      method: 'POST',
      data: {
        actor_type: input.actor.actorType,
        actor_id: input.actor.id,
        action_type: input.actionType,
        target_type: input.target.targetType,
        target_id: input.target.id,
      },
      signal,
    },
  );
}

export function getCommunityInteractionSummary(
  query: InteractionSummaryQuery,
  signal?: AbortSignal,
) {
  return backendRequest<CommunityInteractionEnvelope<InteractionSummaryDto>>(
    `${BBS_INTERACTION_API_BASE}/summary`,
    {
      method: 'GET',
      params: mapInteractionSummaryQuery(query),
      signal,
    },
  );
}

export function getCommunityInteractionSummaryBatch(
  queries: InteractionSummaryQuery[],
  signal?: AbortSignal,
) {
  return backendRequest<CommunityInteractionEnvelope<InteractionSummaryBatchResponseDto>>(
    `${BBS_INTERACTION_API_BASE}/summary/batch`,
    {
      method: 'POST',
      data: { items: queries.map(mapInteractionSummaryQuery) },
      signal,
    },
  );
}
