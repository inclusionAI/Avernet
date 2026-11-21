import type {
  CreateTodoInput,
  CreateTodoInteractionInput,
  TodoAcceptanceInput,
  TodoArtifact,
  TodoEvent,
  TodoListQuery,
  TodoRecord,
  TodoSourceDraft,
  TodoSourceProject,
  TodoSourceWorkItem,
  TodoStatus,
} from '@/domain/todos/models';
import { BackendRequestError } from '../httpClient';
import { isEnvelopeSuccess, type BackendApiEnvelope, type BackendApiPage } from '../types';
import {
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
  runTodo,
  submitTodoAcceptance,
  type TodoArtifactDto,
  type TodoDto,
  type TodoEventDto,
  type TodoSourceDraftDto,
  type TodoSourceProjectDto,
  type TodoSourceWorkItemDto,
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

export async function loadTodos(query: TodoListQuery): Promise<TodoPage> {
  const response = await listTodos(query);
  const data = unwrap(response, '/openapi/v1/collaboration/tasks/todos');
  return readPage(data, query);
}

export async function loadTodo(todoId: string): Promise<TodoRecord> {
  const response = await getTodo(todoId);
  return mapTodoDto(unwrap(response, `/openapi/v1/collaboration/tasks/todos/${encodeURIComponent(todoId)}`));
}

export async function addTodo(input: CreateTodoInput): Promise<TodoRecord> {
  const response = await createTodo(input);
  return mapTodoDto(unwrap(response, '/openapi/v1/collaboration/tasks/todos'));
}

export async function acceptTodo(todoId: string, input: TodoAcceptanceInput): Promise<TodoRecord> {
  const response = await submitTodoAcceptance(todoId, input);
  return mapTodoDto(unwrap(response, `/openapi/v1/collaboration/tasks/todos/${encodeURIComponent(todoId)}/acceptance`));
}

export async function loadTodoInteractions(todoId: string): Promise<TodoEvent[]> {
  const response = await listTodoInteractions(todoId);
  const data = unwrap(response, `/openapi/v1/collaboration/tasks/todos/${encodeURIComponent(todoId)}/interactions`);
  return readList(data);
}

export async function addTodoInteraction(todoId: string, input: CreateTodoInteractionInput): Promise<TodoEvent> {
  const response = await createTodoInteraction(todoId, input);
  const data = unwrap(response, `/openapi/v1/collaboration/tasks/todos/${encodeURIComponent(todoId)}/interactions`);
  return mapTodoEventDto(data);
}

export async function loadTodoEvents(todoId: string): Promise<TodoEvent[]> {
  const response = await listTodoEvents(todoId);
  const data = unwrap(response, `/openapi/v1/collaboration/tasks/todos/${encodeURIComponent(todoId)}/events`);
  return readList(data);
}

export async function startTodoRun(todoId: string): Promise<TodoRecord> {
  const response = await runTodo(todoId);
  const data = unwrap(response, `/openapi/v1/collaboration/tasks/todos/${encodeURIComponent(todoId)}/run`);
  return mapTodoDto(data);
}

export type { TodoStatus };

export async function loadTodoArtifacts(todoId: string): Promise<TodoArtifact[]> {
  const response = await listTodoArtifacts(todoId);
  const data = unwrap(response, `/openapi/v1/collaboration/tasks/todos/${encodeURIComponent(todoId)}/artifacts`);
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

function mapSourceProject(dto: TodoSourceProjectDto, path: string): TodoSourceProject {
  return { id: requiredText(dto.id, path, dto), name: requiredText(dto.name, path, dto) };
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
  };
}

export async function loadTodoSourceProjects(): Promise<TodoSourceProject[]> {
  const path = '/openapi/v1/collaboration/tasks/todos/sources/dima/projects';
  return unwrap(await listTodoSourceProjects(), path).map((item) => mapSourceProject(item, path));
}

export async function loadTodoSourceWorkItems(projectId: string, query?: string): Promise<TodoSourceWorkItem[]> {
  const path = `/openapi/v1/collaboration/tasks/todos/sources/dima/projects/${encodeURIComponent(
    projectId,
  )}/work-items`;
  return unwrap(await listTodoSourceWorkItems(projectId, query), path).map((item) => mapSourceWorkItem(item, path));
}

export async function loadTodoSourceDraft(projectId: string, workItemId: string): Promise<TodoSourceDraft> {
  const path = `/openapi/v1/collaboration/tasks/todos/sources/dima/projects/${encodeURIComponent(
    projectId,
  )}/work-items/${encodeURIComponent(workItemId)}`;
  return mapSourceDraft(unwrap(await getTodoSourceDraft(projectId, workItemId), path), path);
}
