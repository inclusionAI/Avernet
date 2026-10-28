import type { PersonalCenterActivityQuery } from '@/domain/community/types';
import type { PersonalCenterActivityDto } from '@/domain/community/mapper';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage } from '../types';

export type PersonalCenterEnvelope<T> = BackendApiEnvelope<T>;

const PERSONAL_CENTER_API_BASE = `${TASK_READ_API_BASE}/bbs/personal-center/activities`;

export function getPersonalCenterActivities(
  query: PersonalCenterActivityQuery,
  signal?: AbortSignal,
) {
  return backendRequest<PersonalCenterEnvelope<BackendApiPage<PersonalCenterActivityDto>>>(
    PERSONAL_CENTER_API_BASE,
    {
      method: 'GET',
      params: {
        owner_user_id: query.ownerUserId,
        actor_scope: query.actorScope,
        activity_type: query.activityType,
        target_type: query.targetType,
        page: query.page,
        page_size: query.pageSize,
      },
      signal,
    },
  );
}
