import type {
  CreateTodoInput,
  CreateTodoInteractionInput,
  TodoAcceptanceInput,
  TodoListQuery,
  UpdateTodoInput,
} from '@/domain/todos/models';
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
  context_version?: string;
  status?: string | null;
  created_at?: string;
  artifacts?: Array<BackendUnknownRecord>;
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
};

export function listTodos(query: TodoListQuery) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<TodoDto> | TodoDto[]>>(TODO_ENDPOINTS.list, {
    method: 'GET',
    params: {
      space_id: query.spaceId,
      keyword: query.keyword || undefined,
      status: query.status,
      page: query.page,
      page_size: query.pageSize,
    },
    operation: 'list-todos',
    retryOnTransient: true,
  });
}

export function getTodo(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.detail(todoId), {
    method: 'GET',
    operation: 'get-todo',
    retryOnTransient: true,
  });
}

export function createTodo(input: CreateTodoInput) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.list, {
    method: 'POST',
    data: {
      space_id: input.spaceId,
      title: input.title,
      description: input.description,
      bot_id: input.botId,
    },
    operation: 'create-todo',
  });
}

export function updateTodo(todoId: string, input: UpdateTodoInput) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.detail(todoId), {
    method: 'PATCH',
    data: {
      ...(input.title === undefined ? {} : { title: input.title }),
      ...(input.description === undefined ? {} : { description: input.description }),
      ...(input.botId === undefined ? {} : { bot_id: input.botId }),
    },
    operation: 'update-todo',
  });
}

export function submitTodoAcceptance(todoId: string, input: TodoAcceptanceInput) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.acceptance(todoId), {
    method: 'POST',
    data: {
      verdict: input.verdict,
      message: input.message || undefined,
    },
    operation: 'accept-todo',
  });
}

export function listTodoInteractions(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoEventDto[]>>(TODO_ENDPOINTS.interactions(todoId), {
    method: 'GET',
    operation: 'list-todo-interactions',
    retryOnTransient: true,
  });
}

export function createTodoInteraction(todoId: string, input: CreateTodoInteractionInput) {
  return backendRequest<BackendApiEnvelope<TodoEventDto>>(TODO_ENDPOINTS.interactions(todoId), {
    method: 'POST',
    data: { message: input.message },
    operation: 'create-todo-interaction',
  });
}

export function listTodoEvents(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoEventDto[]>>(TODO_ENDPOINTS.events(todoId), {
    method: 'GET',
    operation: 'list-todo-events',
    retryOnTransient: true,
  });
}

export function runTodo(todoId: string) {
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.run(todoId), {
    method: 'POST',
    operation: 'run-todo',
  });
}
