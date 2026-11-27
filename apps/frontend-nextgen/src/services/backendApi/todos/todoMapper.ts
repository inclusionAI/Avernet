import type {
  TodoArtifact,
  TodoEvent,
  TodoProgress,
  TodoProgressStageStatus,
  TodoRecord,
  TodoStageProgress,
  TodoStatus,
} from '@/domain/todos/models';
import type { TodoArtifactDto, TodoDto, TodoEventDto, TodoProgressDto } from './todoController';

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : value === undefined || value === null ? fallback : String(value);
}

function numberValue(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

const TODO_STATUSES = new Set<TodoStatus>([
  'not_started',
  'in_progress',
  'interrupted',
  'pending_action',
  'completed',
  'cancelled',
]);

function statusValue(value: unknown): TodoStatus {
  if (typeof value === 'string' && TODO_STATUSES.has(value as TodoStatus)) {
    return value as TodoStatus;
  }
  throw new Error(`Unsupported Todo status: ${JSON.stringify(value)}`);
}

export function mapTodoDto(dto: TodoDto): TodoRecord {
  const collabContextId = stringValue(dto.collab_context_id);
  return {
    todoId: stringValue(dto.todo_id ?? dto.id),
    spaceId: numberValue(dto.space_id),
    title: stringValue(dto.title, '未命名待办'),
    description: stringValue(dto.description),
    botId: stringValue(dto.bot_id),
    botName: stringValue(dto.bot_name) || undefined,
    status: statusValue(dto.status),
    collabContextId: collabContextId || undefined,
    createdAt: stringValue(dto.created_at ?? dto.gmt_create),
    updatedAt: stringValue(dto.updated_at ?? dto.gmt_modified),
  };
}

function mapArtifact(value: Record<string, unknown>): TodoEvent['artifacts'][number] | null {
  const url = stringValue(value.url ?? value.address);
  const name = stringValue(value.name ?? value.artifact_name, '未命名产物');
  if (!url) return null;
  return {
    artifactId: stringValue(value.artifact_id ?? value.artifactId) || undefined,
    name,
    url,
  };
}

export function mapTodoEventDto(dto: TodoEventDto): TodoEvent {
  const artifacts = Array.isArray(dto.artifacts)
    ? dto.artifacts
        .map(recordValue)
        .map(mapArtifact)
        .filter((item): item is TodoEvent['artifacts'][number] => item !== null)
    : [];
  const status = stringValue(dto.status);
  return {
    eventId: stringValue(dto.event_id),
    eventSeq: numberValue(dto.event_seq),
    eventType: stringValue(dto.event_type, 'event'),
    actorId: stringValue(dto.actor_id),
    actorType: stringValue(dto.actor_type).toLowerCase() || 'human',
    content: stringValue(dto.content),
    executionId: stringValue(dto.execution_id),
    contextVersion:
      dto.context_version === null || dto.context_version === undefined ? undefined : numberValue(dto.context_version),
    status: status || undefined,
    createdAt: stringValue(dto.created_at),
    artifacts,
    payload: recordValue(dto.payload),
  };
}

export function mapTodoArtifactDto(dto: TodoArtifactDto): TodoArtifact | null {
  const artifactId = stringValue(dto.artifact_id);
  const todoId = stringValue(dto.todo_id);
  const executionId = stringValue(dto.todo_execution_id ?? dto.execution_id);
  const artifactName = stringValue(dto.artifact_name, '未命名产物');
  const createdAt = stringValue(dto.created_at);
  const kind = stringValue(dto.kind).toLowerCase();
  const content = recordValue(dto.content);
  const isFinalArtifact = Boolean(content.is_final_artifact);
  const base = { artifactId, todoId, executionId, artifactName, createdAt };

  if (!artifactId || !todoId || !executionId) return null;
  if (kind === 'text') {
    return {
      ...base,
      kind,
      content: { text: stringValue(content.text), isFinalArtifact },
    };
  }
  if (kind === 'file') {
    return {
      ...base,
      kind,
      content: {
        fileName: stringValue(content.file_name, artifactName),
        fileType: stringValue(content.file_type, 'application/octet-stream'),
        size: numberValue(content.size),
        isFinalArtifact,
      },
    };
  }
  if (kind === 'url_link') {
    const type = stringValue(content.type);
    return {
      ...base,
      kind,
      content: {
        url: stringValue(content.url),
        type: type || undefined,
        isFinalArtifact,
      },
    };
  }
  if (kind === 'code') {
    const prLink = stringValue(content.pr_link);
    const prDesc = stringValue(content.pr_desc);
    return {
      ...base,
      kind,
      content: {
        codeBaseName: stringValue(content.code_base_name, artifactName),
        codeBaseAddress: stringValue(content.code_base_address),
        prLink: prLink || undefined,
        prDesc: prDesc || undefined,
        isFinalArtifact,
      },
    };
  }
  return null;
}

const TODO_PROGRESS_STAGE_STATUSES = new Set<TodoProgressStageStatus>([
  'not_started',
  'in_progress',
  'pending_action',
  'interrupted',
  'completed',
  'cancelled',
  'unknown',
]);

const TODO_PROGRESS_RUNTIME_KINDS = new Set<TodoProgress['runtimeKind']>([
  'product_collet',
  'product_runtime_pending',
  'legacy_context',
  'invalid_binding',
]);

function optionalString(value: unknown): string | undefined {
  const result = stringValue(value);
  return result || undefined;
}

function optionalBoolean(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function progressStatus(value: unknown): TodoProgressStageStatus {
  if (typeof value === 'string' && TODO_PROGRESS_STAGE_STATUSES.has(value as TodoProgressStageStatus)) {
    return value as TodoProgressStageStatus;
  }
  throw new Error(`Unsupported Todo progress stage status: ${JSON.stringify(value)}`);
}

function mapTodoProgressStageDto(dto: Record<string, unknown>): TodoStageProgress {
  if (!stringValue(dto.stage_instance_id) || !stringValue(dto.code) || !stringValue(dto.name)) {
    throw new Error('Todo progress stage identity fields are missing');
  }
  const blockerValue = recordValue(dto.blocker ?? {});
  const blocker: TodoStageProgress['blocker'] = Object.keys(blockerValue).length
    ? {
        reason: optionalString(blockerValue.reason),
        requestedAction: optionalString(blockerValue.requested_action),
        errorCode: optionalString(blockerValue.error_code),
      }
    : undefined;
  return {
    stageInstanceId: stringValue(dto.stage_instance_id),
    code: stringValue(dto.code),
    name: stringValue(dto.name),
    order: numberValue(dto.order),
    kind: stringValue(dto.kind),
    status: progressStatus(dto.status),
    ownerUserId: stringValue(dto.owner_user_id),
    ownerUserName: stringValue(dto.owner_user_name),
    executorSubjectId: stringValue(dto.executor_subject_id),
    executorSubjectName: stringValue(dto.executor_subject_name),
    summary: stringValue(dto.summary),
    currentStep: optionalString(dto.current_step),
    steps: (Array.isArray(dto.steps) ? dto.steps : []).map(recordValue).map((step) => ({
      stepId: stringValue(step.step_id),
      name: stringValue(step.name),
      status: progressStatus(step.status),
      detail: stringValue(step.detail),
      occurredAt: optionalString(step.occurred_at),
    })),
    outputs: (Array.isArray(dto.outputs) ? dto.outputs : []).map(recordValue).map((output) => ({
      artifactId: optionalString(output.artifact_id),
      kind: stringValue(output.kind, 'artifact'),
      name: stringValue(output.name),
      summary: stringValue(output.summary),
    })),
    blocker,
    attemptCount: numberValue(dto.attempt_count),
    detailsLoaded: Boolean(dto.details_loaded),
    messagesComplete: optionalBoolean(dto.messages_complete),
    ignoredMessageCount: numberValue(dto.ignored_message_count),
    updatedAt: optionalString(dto.updated_at),
  };
}

export function mapTodoProgressDto(dto: TodoProgressDto): TodoProgress {
  const runtimeKind = stringValue(dto.runtime_kind);
  if (!TODO_PROGRESS_RUNTIME_KINDS.has(runtimeKind as TodoProgress['runtimeKind'])) {
    throw new Error(`Unsupported Todo runtime kind: ${JSON.stringify(dto.runtime_kind)}`);
  }
  const status = statusValue(dto.status);
  if (!stringValue(dto.todo_id)) throw new Error('Todo progress identity is missing');
  return {
    todoId: stringValue(dto.todo_id),
    status,
    runtimeKind: runtimeKind as TodoProgress['runtimeKind'],
    runtimeUnavailable: Boolean(dto.runtime_unavailable),
    runtimeErrorCode: optionalString(dto.runtime_error_code),
    currentStageCode: optionalString(dto.current_stage_code),
    rolloutRevision: dto.rollout_revision === null || dto.rollout_revision === undefined ? undefined : numberValue(dto.rollout_revision),
    stopped: optionalBoolean(dto.stopped),
    stages: (Array.isArray(dto.stages) ? dto.stages : []).map(recordValue).map(mapTodoProgressStageDto),
  };
}
