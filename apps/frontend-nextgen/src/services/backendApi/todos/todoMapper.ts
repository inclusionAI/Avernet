import type { TodoArtifact, TodoEvent, TodoRecord, TodoStatus } from '@/domain/todos/models';
import type { TodoArtifactDto, TodoDto, TodoEventDto } from './todoController';

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

function statusValue(value: unknown): TodoStatus {
  const status = stringValue(value).toLowerCase();
  if (status === 'pending' || status === 'in_progress' || status === 'pending_acceptance' || status === 'completed') {
    return status;
  }
  return 'pending';
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
  };
}

export function mapTodoArtifactDto(dto: TodoArtifactDto): TodoArtifact | null {
  const artifactId = stringValue(dto.artifact_id);
  const todoId = stringValue(dto.todo_id);
  const executionId = stringValue(dto.execution_id);
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
