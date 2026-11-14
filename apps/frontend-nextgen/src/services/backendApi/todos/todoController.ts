import type {
  CreateTodoInput,
  CreateTodoInteractionInput,
  TodoAcceptanceInput,
  TodoListQuery,
} from '@/domain/todos/models';
import { userScopedParams } from '../bots/botController';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

const TODO_API_BASE = '/openapi/v1/todos';

export interface TodoDto extends BackendUnknownRecord {
  todo_id?: string | number;
  id?: string | number;
  space_id?: number | string;
  title?: string;
  description?: string;
  bot_id?: string;
  bot_name?: string;
  status?: string;
  collab_context_id?: string;
  created_at?: string;
  updated_at?: string;
  gmt_create?: string;
  gmt_modified?: string;
}

export interface TodoEventDto extends BackendUnknownRecord {
  event_id?: string | number;
  event_seq?: number;
  event_type?: string;
  actor_id?: string;
  actor_type?: string;
  content?: string;
  execution_id?: string;
  context_version?: number | null;
  status?: string | null;
  created_at?: string;
  artifacts?: Array<BackendUnknownRecord>;
  payload?: BackendUnknownRecord;
}

export interface TodoArtifactDto extends BackendUnknownRecord {
  artifact_id?: string;
  todo_id?: string;
  execution_id?: string;
  artifact_name?: string;
  kind?: string;
  content?: BackendUnknownRecord;
  created_at?: string;
}

export interface TodoPageDto {
  items?: TodoDto[];
  total?: number;
  page?: number;
  page_size?: number;
  pageSize?: number;
}

export const TODO_ENDPOINTS = {
  list: TODO_API_BASE,
  detail: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}`,
  acceptance: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/acceptance`,
  interactions: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/interactions`,
  events: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/events`,
  run: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/run`,
  artifacts: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/artifacts`,
  artifactContent: (todoId: string, artifactId: string) =>
    `${TODO_API_BASE}/${encodeURIComponent(todoId)}/artifacts/${encodeURIComponent(artifactId)}/content`,
};

export function listTodos(query: TodoListQuery) {
  const listingParams: BackendUnknownRecord = {
    space_id: query.spaceId,
    page: query.page,
    page_size: query.pageSize,
  };
  if (query.keyword) listingParams.keyword = query.keyword;
  if (query.status) listingParams.status = query.status;
  if (query.botId) listingParams.bot_id = query.botId;

  return backendRequest<BackendApiEnvelope<BackendApiPage<TodoDto> | TodoDto[]>>(TODO_ENDPOINTS.list, {
    method: 'GET',
    params: userScopedParams(listingParams),
    operation: 'list-todos',
    retryOnTransient: true,
  });
}

export function getTodo(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.detail(todoId), {
    method: 'GET',
    params: userScopedParams(),
    operation: 'get-todo',
    retryOnTransient: true,
  });
}

export function createTodo(input: CreateTodoInput) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.list, {
    method: 'POST',
    params: userScopedParams(),
    data: {
      space_id: input.spaceId,
      title: input.title,
      description: input.description,
      bot_id: input.botId,
    },
    operation: 'create-todo',
  });
}

export function submitTodoAcceptance(todoId: string, input: TodoAcceptanceInput) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.acceptance(todoId), {
    method: 'POST',
    params: userScopedParams(),
    data: {
      decision_id: input.decisionId,
      verdict: input.verdict,
      message: input.message || undefined,
      targets: input.targets.map((target) => ({
        kind: target.kind,
        id: target.id,
        floor: target.floor,
      })),
    },
    operation: 'accept-todo',
  });
}

export function listTodoInteractions(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoEventDto[]>>(TODO_ENDPOINTS.interactions(todoId), {
    method: 'GET',
    params: userScopedParams(),
    operation: 'list-todo-interactions',
    retryOnTransient: true,
  });
}

export function createTodoInteraction(todoId: string, input: CreateTodoInteractionInput) {
  return backendRequest<BackendApiEnvelope<TodoEventDto>>(TODO_ENDPOINTS.interactions(todoId), {
    method: 'POST',
    params: userScopedParams(),
    data: { message: input.message },
    operation: 'create-todo-interaction',
  });
}

export function listTodoEvents(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoEventDto[]>>(TODO_ENDPOINTS.events(todoId), {
    method: 'GET',
    params: userScopedParams(),
    operation: 'list-todo-events',
    retryOnTransient: true,
  });
}

export function runTodo(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.run(todoId), {
    method: 'POST',
    params: userScopedParams(),
    operation: 'run-todo',
  });
}

export function listTodoArtifacts(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoArtifactDto[]>>(TODO_ENDPOINTS.artifacts(todoId), {
    method: 'GET',
    params: userScopedParams(),
    operation: 'list-todo-artifacts',
    retryOnTransient: true,
  });
}

export function downloadTodoArtifactContent(
  todoId: string,
  artifactId: string,
  disposition: 'inline' | 'attachment' = 'attachment',
): Promise<Blob> {
  return backendRequest<Blob>(TODO_ENDPOINTS.artifactContent(todoId, artifactId), {
    method: 'GET',
    params: userScopedParams({ disposition }),
    responseType: 'blob',
    operation: 'download-todo-artifact-content',
  });
}
