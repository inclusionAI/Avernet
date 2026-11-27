import type {
  CreateTodoAutoImportRuleInput,
  CreateTodoInput,
  CreateTodoInteractionInput,
  TodoAcceptanceInput,
  TodoListQuery,
  TodoAutoImportRuleStatus,
  UpdateTodoAutoImportRuleInput,
} from '@/domain/todos/models';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { userScopedParams } from '../bots/botController';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

const TODO_API_BASE = `${TASK_READ_API_BASE}/todos`;
// Space 列表是 Task Engine 内部契约，不存在对应 OpenAPI；保持同源相对路径，由本地/部署代理转发到 Engine。
const TODO_SPACES_API_PATH = '/api/v1/collaboration/tasks/spaces';

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
  todo_execution_id?: string;
  /** @deprecated 兼容旧版响应；Todo 产物标准字段为 todo_execution_id。 */
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

export interface TodoProgressDto extends BackendUnknownRecord {
  todo_id?: string;
  status?: string;
  runtime_kind?: string;
  runtime_unavailable?: boolean;
  runtime_error_code?: string | null;
  current_stage_code?: string | null;
  rollout_revision?: number | null;
  stopped?: boolean | null;
  stages?: Array<BackendUnknownRecord>;
}

export interface TodoSpaceDto extends BackendUnknownRecord {
  space_id?: number | string;
  id?: number | string;
  space_name?: string;
  name?: string;
  space_type?: string;
  type?: string;
}

export interface TodoSpacePageDto extends BackendApiPage<TodoSpaceDto> {
  list?: TodoSpaceDto[];
  spaces?: TodoSpaceDto[];
  page_no?: number;
}

export interface TodoSourceProjectDto extends BackendUnknownRecord {
  id?: string | number;
  name?: string;
  kind?: string;
}

export interface TodoSourceWorkItemDto extends BackendUnknownRecord {
  id?: string | number;
  project_id?: string | number;
  category?: string;
  title?: string;
  status?: string | null;
  updated_at?: string | null;
}

export interface TodoSourceDraftDto extends BackendUnknownRecord {
  source_type?: string;
  project_id?: string | number;
  work_item_id?: string | number;
  category?: string;
  title?: string;
  description?: string;
  source_url?: string | null;
  source_updated_at?: string | null;
  metadata?: BackendUnknownRecord;
}

export interface TodoAutoImportRuleDto extends BackendUnknownRecord {
  rule_id?: string;
  space_id?: number | string;
  source_container_ids?: unknown[];
  filter_text?: string;
  bot_id?: string;
  status?: string;
  cursor_updated_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export const TODO_ENDPOINTS = {
  spaces: TODO_SPACES_API_PATH,
  list: TODO_API_BASE,
  detail: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}`,
  progress: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/progress`,
  acceptance: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/acceptance`,
  interactions: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/interactions`,
  events: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/events`,
  run: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/run`,
  artifacts: (todoId: string) => `${TODO_API_BASE}/${encodeURIComponent(todoId)}/artifacts`,
  artifactContent: (todoId: string, artifactId: string) =>
    `${TODO_API_BASE}/${encodeURIComponent(todoId)}/artifacts/${encodeURIComponent(artifactId)}/content`,
  dimaProjects: `${TODO_API_BASE}/sources/dima/projects`,
  dimaWorkItems: (projectId: string) =>
    `${TODO_API_BASE}/sources/dima/projects/${encodeURIComponent(projectId)}/work-items`,
  dimaWorkItem: (projectId: string, workItemId: string) =>
    `${TODO_API_BASE}/sources/dima/projects/${encodeURIComponent(projectId)}/work-items/${encodeURIComponent(
      workItemId,
    )}`,
  dimaAutoImportRules: `${TODO_API_BASE}/sources/dima/auto-import-rules`,
  dimaAutoImportRule: (ruleId: string) =>
    `${TODO_API_BASE}/sources/dima/auto-import-rules/${encodeURIComponent(ruleId)}`,
};

export function listTodoSpaces() {
  return backendRequest<BackendApiEnvelope<TodoSpacePageDto | TodoSpaceDto[]>>(TODO_ENDPOINTS.spaces, {
    method: 'GET',
    params: { page_no: 1, page_size: 100, scope: 'accessible' },
    operation: 'list-todo-spaces',
    retryOnTransient: true,
  });
}

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
export function getTodoProgress(todoId: string, options?: { expandedStageCode?: string; messageLimit?: number }) {
  const progressParams: BackendUnknownRecord = {};
  if (options?.expandedStageCode) progressParams.expanded_stage_code = options.expandedStageCode;
  if (options?.messageLimit) progressParams.message_limit = options.messageLimit;
  return backendRequest<BackendApiEnvelope<TodoProgressDto>>(TODO_ENDPOINTS.progress(todoId), {
    method: 'GET',
    params: userScopedParams(progressParams),
    operation: 'get-todo-progress',
    retryOnTransient: true,
  });
}

export function createTodo(input: CreateTodoInput) {
  const data: BackendUnknownRecord = {
    space_id: input.spaceId,
    title: input.title,
    description: input.description,
    bot_id: input.botId,
  };
  if (input.source) {
    data.source = {
      type: input.source.type,
      container_id: input.source.containerId,
      item_id: input.source.itemId,
      url: input.source.url,
      updated_at: input.source.updatedAt,
      metadata: input.source.metadata,
    };
  }
  return backendRequest<BackendApiEnvelope<TodoDto>>(TODO_ENDPOINTS.list, {
    method: 'POST',
    params: userScopedParams(),
    data,
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
  const data: BackendUnknownRecord = { message: input.message };
  if (input.replyToEventId) data.reply_to_event_id = input.replyToEventId;
  if (input.mentions?.length) {
    data.mentions = input.mentions.map((mention) => ({
      actor_type: mention.actorType,
      actor_id: mention.actorId,
    }));
  }
  return backendRequest<BackendApiEnvelope<TodoEventDto>>(TODO_ENDPOINTS.interactions(todoId), {
    method: 'POST',
    params: userScopedParams(),
    data,
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

export function listTodoSourceProjects() {
  return backendRequest<BackendApiEnvelope<TodoSourceProjectDto[]>>(TODO_ENDPOINTS.dimaProjects, {
    method: 'GET',
    params: userScopedParams(),
    operation: 'list-todo-source-projects',
    retryOnTransient: true,
  });
}

export function listTodoSourceWorkItems(projectId: string, query?: string) {
  return backendRequest<BackendApiEnvelope<TodoSourceWorkItemDto[]>>(TODO_ENDPOINTS.dimaWorkItems(projectId), {
    method: 'GET',
    params: userScopedParams(query?.trim() ? { query: query.trim() } : {}),
    operation: 'list-todo-source-work-items',
    retryOnTransient: true,
  });
}

export function getTodoSourceDraft(projectId: string, workItemId: string) {
  return backendRequest<BackendApiEnvelope<TodoSourceDraftDto>>(TODO_ENDPOINTS.dimaWorkItem(projectId, workItemId), {
    method: 'GET',
    params: userScopedParams(),
    operation: 'get-todo-source-draft',
    retryOnTransient: true,
  });
}

export function createTodoAutoImportRule(input: CreateTodoAutoImportRuleInput) {
  return backendRequest<BackendApiEnvelope<TodoAutoImportRuleDto>>(TODO_ENDPOINTS.dimaAutoImportRules, {
    method: 'POST',
    params: userScopedParams(),
    data: {
      space_id: input.spaceId,
      source_container_ids: input.sourceContainerIds,
      filter_text: input.filterText,
      bot_id: input.botId,
    },
    operation: 'create-todo-auto-import-rule',
  });
}

export function listTodoAutoImportRules(spaceId: number, status?: TodoAutoImportRuleStatus) {
  return backendRequest<BackendApiEnvelope<TodoAutoImportRuleDto[]>>(TODO_ENDPOINTS.dimaAutoImportRules, {
    method: 'GET',
    params: userScopedParams({
      space_id: spaceId,
      ...(status ? { status } : {}),
    }),
    operation: 'list-todo-auto-import-rules',
    retryOnTransient: true,
  });
}

export function updateTodoAutoImportRule(ruleId: string, input: UpdateTodoAutoImportRuleInput) {
  const data: BackendUnknownRecord = {};
  if (input.sourceContainerIds) data.source_container_ids = input.sourceContainerIds;
  if (input.filterText !== undefined) data.filter_text = input.filterText;
  if (input.botId !== undefined) data.bot_id = input.botId;
  if (input.status !== undefined) data.status = input.status;
  return backendRequest<BackendApiEnvelope<TodoAutoImportRuleDto>>(TODO_ENDPOINTS.dimaAutoImportRule(ruleId), {
    method: 'PATCH',
    params: userScopedParams(),
    data,
    operation: 'update-todo-auto-import-rule',
  });
}
