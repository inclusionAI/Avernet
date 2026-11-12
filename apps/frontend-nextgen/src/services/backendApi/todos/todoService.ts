import type {
  CreateTodoInput,
  CreateTodoInteractionInput,
  TodoAcceptanceInput,
  TodoArtifact,
  TodoEvent,
  TodoListQuery,
  TodoRecord,
  TodoStatus,
} from '@/domain/todos/models';
import { BackendRequestError } from '../httpClient';
import { isEnvelopeSuccess, type BackendApiEnvelope, type BackendApiPage } from '../types';
import {
  createTodo,
  createTodoInteraction,
  getTodo,
  listTodoArtifacts,
  listTodoEvents,
  listTodoInteractions,
  listTodos,
  downloadTodoArtifactContent as downloadTodoArtifactContentRequest,
  runTodo,
  submitTodoAcceptance,
  type TodoArtifactDto,
  type TodoDto,
  type TodoEventDto,
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
  const data = unwrap(response, '/openapi/v1/todos');
  return readPage(data, query);
}

export async function loadTodo(todoId: string): Promise<TodoRecord> {
  const response = await getTodo(todoId);
  return mapTodoDto(unwrap(response, `/openapi/v1/todos/${encodeURIComponent(todoId)}`));
}

export async function addTodo(input: CreateTodoInput): Promise<TodoRecord> {
  const response = await createTodo(input);
  return mapTodoDto(unwrap(response, '/openapi/v1/todos'));
}

export async function acceptTodo(todoId: string, input: TodoAcceptanceInput): Promise<TodoRecord> {
  const response = await submitTodoAcceptance(todoId, input);
  return mapTodoDto(unwrap(response, `/openapi/v1/todos/${encodeURIComponent(todoId)}/acceptance`));
}

export async function loadTodoInteractions(todoId: string): Promise<TodoEvent[]> {
  const response = await listTodoInteractions(todoId);
  const data = unwrap(response, `/openapi/v1/todos/${encodeURIComponent(todoId)}/interactions`);
  return readList(data);
}

export async function addTodoInteraction(todoId: string, input: CreateTodoInteractionInput): Promise<TodoEvent> {
  const response = await createTodoInteraction(todoId, input);
  const data = unwrap(response, `/openapi/v1/todos/${encodeURIComponent(todoId)}/interactions`);
  return mapTodoEventDto(data);
}

export async function loadTodoEvents(todoId: string): Promise<TodoEvent[]> {
  const response = await listTodoEvents(todoId);
  const data = unwrap(response, `/openapi/v1/todos/${encodeURIComponent(todoId)}/events`);
  return readList(data);
}

export async function startTodoRun(todoId: string): Promise<TodoRecord> {
  const response = await runTodo(todoId);
  const data = unwrap(response, `/openapi/v1/todos/${encodeURIComponent(todoId)}/run`);
  return mapTodoDto(data);
}

export type { TodoStatus };

export async function loadTodoArtifacts(todoId: string): Promise<TodoArtifact[]> {
  const response = await listTodoArtifacts(todoId);
  const data = unwrap(response, `/openapi/v1/todos/${encodeURIComponent(todoId)}/artifacts`);
  return readArtifacts(data);
}

export function downloadTodoArtifactContent(
  todoId: string,
  artifactId: string,
  disposition: 'inline' | 'attachment' = 'attachment',
): Promise<Blob> {
  return downloadTodoArtifactContentRequest(todoId, artifactId, disposition);
}
