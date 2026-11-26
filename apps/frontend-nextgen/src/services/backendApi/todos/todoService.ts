import type {
  CreateTodoAutoImportRuleInput,
  CreateTodoInput,
  CreateTodoInteractionInput,
  TodoAcceptanceInput,
  TodoArtifact,
  TodoAutoImportRule,
  TodoEvent,
  TodoListQuery,
  TodoRecord,
  TodoSourceDraft,
  TodoSourceProject,
  TodoSourceWorkItem,
  TodoSpace,
  TodoStatus,
  UpdateTodoAutoImportRuleInput,
} from '@/domain/todos/models';
import { BackendRequestError } from '../httpClient';
import { isEnvelopeSuccess, type BackendApiEnvelope, type BackendApiPage } from '../types';
import {
  createTodoAutoImportRule as createTodoAutoImportRuleRequest,
  createTodo,
  createTodoInteraction,
  downloadTodoArtifactContent as downloadTodoArtifactContentRequest,
  getTodo,
  getTodoSourceDraft,
  listTodoArtifacts,
  listTodoEvents,
  listTodoInteractions,
  listTodos,
  listTodoSourceProjects,
  listTodoSourceWorkItems,
  listTodoAutoImportRules,
  listTodoSpaces,
  runTodo,
  submitTodoAcceptance,
  TODO_ENDPOINTS,
  updateTodoAutoImportRule as updateTodoAutoImportRuleRequest,
  type TodoAutoImportRuleDto,
  type TodoArtifactDto,
  type TodoDto,
  type TodoEventDto,
  type TodoSourceDraftDto,
  type TodoSourceProjectDto,
  type TodoSourceWorkItemDto,
  type TodoSpaceDto,
  type TodoSpacePageDto,
} from './todoController';
import { mapTodoArtifactDto, mapTodoDto, mapTodoEventDto } from './todoMapper';

export interface TodoPage {
  items: TodoRecord[];
  total: number;
  page: number;
  pageSize: number;
}

function invalidResponse(path: string, message: string, response?: unknown): BackendRequestError {
  return new BackendRequestError(message, { apiPath: path, data: response });
}

function unwrap<T>(response: BackendApiEnvelope<T>, path: string): T {
  if (!isEnvelopeSuccess(response) || response.data === undefined || response.data === null) {
    throw invalidResponse(path, response.message || '待办接口响应无效', response);
  }
  return response.data;
}

function readPage(data: BackendApiPage<TodoDto> | TodoDto[], query: TodoListQuery): TodoPage {
  const items = Array.isArray(data) ? data : data.items ?? [];
  const total = Array.isArray(data) ? items.length : data.total ?? items.length;
  return {
    items: items.map(mapTodoDto),
    total,
    page: Array.isArray(data) ? query.page : data.page ?? query.page,
    pageSize: Array.isArray(data) ? query.pageSize : data.pageSize ?? data.page_size ?? query.pageSize,
  };
}

function readList(data: TodoEventDto[]): TodoEvent[] {
  return data.map(mapTodoEventDto);
}

function readArtifacts(data: TodoArtifactDto[]): TodoArtifact[] {
  return data.map(mapTodoArtifactDto).filter((item): item is TodoArtifact => item !== null);
}

function todoSpaceId(dto: TodoSpaceDto): number {
  const raw = dto.space_id ?? dto.id;
  const value = typeof raw === 'number' ? raw : typeof raw === 'string' && raw.trim() ? Number(raw) : Number.NaN;
  if (!Number.isInteger(value) || value <= 0) {
    throw invalidResponse(TODO_ENDPOINTS.spaces, '空间列表响应无效', dto);
  }
  return value;
}

function mapTodoSpace(dto: TodoSpaceDto): TodoSpace {
  const spaceId = todoSpaceId(dto);
  const rawName = dto.space_name ?? dto.name;
  const spaceName = typeof rawName === 'string' && rawName.trim() ? rawName.trim() : `空间 ${spaceId}`;
  const rawType = dto.space_type ?? dto.type;
  const normalizedType = typeof rawType === 'string' ? rawType.trim().toUpperCase() : '';
  const spaceType = normalizedType === 'TEAM' || normalizedType === 'PERSONAL' ? normalizedType : 'UNKNOWN';
  return { spaceId, spaceName, spaceType };
}

function readTodoSpaces(data: TodoSpacePageDto | TodoSpaceDto[]): TodoSpace[] {
  const rows = Array.isArray(data) ? data : data.items ?? data.list ?? data.spaces ?? [];
  return rows.map(mapTodoSpace);
}

export async function loadTodoSpaces(): Promise<TodoSpace[]> {
  return readTodoSpaces(unwrap(await listTodoSpaces(), TODO_ENDPOINTS.spaces));
}

export async function loadTodos(query: TodoListQuery): Promise<TodoPage> {
  const response = await listTodos(query);
  const data = unwrap(response, TODO_ENDPOINTS.list);
  return readPage(data, query);
}

export async function loadTodo(todoId: string): Promise<TodoRecord> {
  const response = await getTodo(todoId);
  return mapTodoDto(unwrap(response, TODO_ENDPOINTS.detail(todoId)));
}

export async function addTodo(input: CreateTodoInput): Promise<TodoRecord> {
  const response = await createTodo(input);
  return mapTodoDto(unwrap(response, TODO_ENDPOINTS.list));
}

export async function acceptTodo(todoId: string, input: TodoAcceptanceInput): Promise<TodoRecord> {
  const response = await submitTodoAcceptance(todoId, input);
  return mapTodoDto(unwrap(response, TODO_ENDPOINTS.acceptance(todoId)));
}

export async function loadTodoInteractions(todoId: string): Promise<TodoEvent[]> {
  const response = await listTodoInteractions(todoId);
  const data = unwrap(response, TODO_ENDPOINTS.interactions(todoId));
  return readList(data);
}

export async function addTodoInteraction(todoId: string, input: CreateTodoInteractionInput): Promise<TodoEvent> {
  const response = await createTodoInteraction(todoId, input);
  const data = unwrap(response, TODO_ENDPOINTS.interactions(todoId));
  return mapTodoEventDto(data);
}

export async function loadTodoEvents(todoId: string): Promise<TodoEvent[]> {
  const response = await listTodoEvents(todoId);
  const data = unwrap(response, TODO_ENDPOINTS.events(todoId));
  return readList(data);
}

export async function startTodoRun(todoId: string): Promise<TodoRecord> {
  const response = await runTodo(todoId);
  const data = unwrap(response, TODO_ENDPOINTS.run(todoId));
  return mapTodoDto(data);
}

export type { TodoStatus };

export async function loadTodoArtifacts(todoId: string): Promise<TodoArtifact[]> {
  const response = await listTodoArtifacts(todoId);
  const data = unwrap(response, TODO_ENDPOINTS.artifacts(todoId));
  return readArtifacts(data);
}

export function downloadTodoArtifactContent(
  todoId: string,
  artifactId: string,
  disposition: 'inline' | 'attachment' = 'attachment',
): Promise<Blob> {
  return downloadTodoArtifactContentRequest(todoId, artifactId, disposition);
}

function requiredText(value: unknown, path: string, response: unknown): string {
  if (typeof value !== 'string' && typeof value !== 'number') {
    throw invalidResponse(path, 'Dima 待办来源响应无效', response);
  }
  const text = String(value).trim();
  if (!text) throw invalidResponse(path, 'Dima 待办来源响应无效', response);
  return text;
}

function sourceCategory(value: unknown, path: string, response: unknown): 'req' | 'task' | 'bug' {
  if (value === 'req' || value === 'task' || value === 'bug') return value;
  throw invalidResponse(path, 'Dima 待办来源响应无效', response);
}

function sourceMetadata(value: unknown, path: string, response: unknown): Record<string, string> {
  if (value === undefined || value === null) return {};
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw invalidResponse(path, 'Dima 待办来源响应无效', response);
  }
  const metadata: Record<string, string> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== 'string') throw invalidResponse(path, 'Dima 待办来源响应无效', response);
    metadata[key] = item;
  }
  return metadata;
}

function mapSourceProject(dto: TodoSourceProjectDto, path: string): TodoSourceProject {
  const id = requiredText(dto.id, path, dto);
  const kind =
    dto.kind === 'workspace' || dto.kind === 'project'
      ? dto.kind
      : id.toUpperCase().startsWith('W')
      ? 'workspace'
      : 'project';
  return { id, name: requiredText(dto.name, path, dto), kind };
}

function mapSourceWorkItem(dto: TodoSourceWorkItemDto, path: string): TodoSourceWorkItem {
  return {
    id: requiredText(dto.id, path, dto),
    projectId: requiredText(dto.project_id, path, dto),
    category: sourceCategory(dto.category, path, dto),
    title: requiredText(dto.title, path, dto),
    status: typeof dto.status === 'string' && dto.status.trim() ? dto.status : undefined,
    updatedAt: typeof dto.updated_at === 'string' && dto.updated_at.trim() ? dto.updated_at : undefined,
  };
}

function mapSourceDraft(dto: TodoSourceDraftDto, path: string): TodoSourceDraft {
  if (dto.source_type !== 'dima') throw invalidResponse(path, 'Dima 待办来源响应无效', dto);
  return {
    sourceType: 'dima',
    projectId: requiredText(dto.project_id, path, dto),
    workItemId: requiredText(dto.work_item_id, path, dto),
    category: sourceCategory(dto.category, path, dto),
    title: requiredText(dto.title, path, dto),
    description: typeof dto.description === 'string' ? dto.description : '',
    sourceUrl: typeof dto.source_url === 'string' && dto.source_url.trim() ? dto.source_url : undefined,
    sourceUpdatedAt:
      typeof dto.source_updated_at === 'string' && dto.source_updated_at.trim() ? dto.source_updated_at : undefined,
    metadata: sourceMetadata(dto.metadata, path, dto),
  };
}

function mapAutoImportRule(dto: TodoAutoImportRuleDto, path: string): TodoAutoImportRule {
  const spaceId = Number(dto.space_id);
  if (!Number.isInteger(spaceId) || spaceId <= 0) {
    throw invalidResponse(path, 'Dima 自动导入规则响应无效', dto);
  }
  if (!Array.isArray(dto.source_container_ids) || dto.source_container_ids.length === 0) {
    throw invalidResponse(path, 'Dima 自动导入规则响应无效', dto);
  }
  const sourceContainerIds = dto.source_container_ids.map((item) =>
    requiredText(item, path, dto),
  );
  if (dto.status !== 'enabled' && dto.status !== 'disabled') {
    throw invalidResponse(path, 'Dima 自动导入规则响应无效', dto);
  }
  return {
    ruleId: requiredText(dto.rule_id, path, dto),
    spaceId,
    sourceContainerIds,
    filterText: requiredText(dto.filter_text, path, dto),
    botId: requiredText(dto.bot_id, path, dto),
    status: dto.status,
    cursorUpdatedAt:
      typeof dto.cursor_updated_at === 'string' && dto.cursor_updated_at.trim() ? dto.cursor_updated_at : undefined,
    createdAt: requiredText(dto.created_at, path, dto),
    updatedAt: requiredText(dto.updated_at, path, dto),
  };
}

export async function loadTodoSourceProjects(): Promise<TodoSourceProject[]> {
  const path = TODO_ENDPOINTS.dimaProjects;
  return unwrap(await listTodoSourceProjects(), path).map((item) => mapSourceProject(item, path));
}

export async function loadTodoSourceWorkItems(projectId: string, query?: string): Promise<TodoSourceWorkItem[]> {
  const path = TODO_ENDPOINTS.dimaWorkItems(projectId);
  return unwrap(await listTodoSourceWorkItems(projectId, query), path).map((item) => mapSourceWorkItem(item, path));
}

export async function loadTodoSourceDraft(projectId: string, workItemId: string): Promise<TodoSourceDraft> {
  const path = TODO_ENDPOINTS.dimaWorkItem(projectId, workItemId);
  return mapSourceDraft(unwrap(await getTodoSourceDraft(projectId, workItemId), path), path);
}

const AUTO_IMPORT_RULES_PATH = '/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules';

export async function createTodoAutoImportRule(input: CreateTodoAutoImportRuleInput): Promise<TodoAutoImportRule> {
  return mapAutoImportRule(unwrap(await createTodoAutoImportRuleRequest(input), AUTO_IMPORT_RULES_PATH), AUTO_IMPORT_RULES_PATH);
}

export async function loadTodoAutoImportRules(spaceId: number): Promise<TodoAutoImportRule[]> {
  return unwrap(await listTodoAutoImportRules(spaceId), AUTO_IMPORT_RULES_PATH).map((item) =>
    mapAutoImportRule(item, AUTO_IMPORT_RULES_PATH),
  );
}

export async function updateTodoAutoImportRule(
  ruleId: string,
  input: UpdateTodoAutoImportRuleInput,
): Promise<TodoAutoImportRule> {
  const path = `${AUTO_IMPORT_RULES_PATH}/${encodeURIComponent(ruleId)}`;
  return mapAutoImportRule(unwrap(await updateTodoAutoImportRuleRequest(ruleId, input), path), path);
}
