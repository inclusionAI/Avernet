import type {
  BotTeamDetail,
  BotTeamNode,
  BotTeamRecord,
  BotTeamRevision,
  DimaImportResult,
  TodoArtifact,
  TodoEvent,
  TodoProductInteraction,
  TodoProductInteractionDeliveryState,
  TodoProductInteractionKind,
  TodoProductInteractionPage,
  TodoProductInteractionResponseType,
  TodoProgress,
  TodoProgressStageStatus,
  TodoRecord,
  TodoStageProgress,
  TodoStatus,
} from '@/domain/todos/models';
import type {
  BotTeamDetailDto,
  BotTeamNodeDto,
  BotTeamRecordDto,
  BotTeamRevisionDto,
  DimaImportResultDto,
  TodoArtifactDto,
  TodoDto,
  TodoEventDto,
  TodoProductInteractionDto,
  TodoProductInteractionPageDto,
  TodoProgressDto,
} from './todoController';

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
    ownerId: stringValue(dto.owner_id) || undefined,
    ownerName: stringValue(dto.owner_name) || undefined,
    templateId: stringValue(dto.template_id) || undefined,
    templateVersionId: stringValue(dto.template_version_id) || undefined,
    templateName: stringValue(dto.template_name_snapshot) || undefined,
    status: statusValue(dto.status),
    collabContextId: collabContextId || undefined,
    createdAt: stringValue(dto.created_at ?? dto.gmt_create),
    updatedAt: stringValue(dto.updated_at ?? dto.gmt_modified),
  };
}

const BOT_TEAM_STATUSES = new Set<BotTeamRecord['status']>(['active', 'archived']);
const DIMA_IMPORT_STATUSES = new Set<DimaImportResult['status']>(['imported', 'duplicate', 'failed']);
const PRODUCT_RUNTIME_STATUSES = new Set<DimaImportResult['productRuntimeStatus']>([
  'ready',
  'retryable_failure',
  'not_applicable',
  'binding_conflict',
  'invalid_binding',
]);

function requiredString(value: unknown, field: string): string {
  const result = stringValue(value).trim();
  if (!result) throw new Error(`Bot 小队字段缺失: ${field}`);
  return result;
}

function positiveInteger(value: unknown, field: string): number {
  const result = numberValue(value);
  if (!Number.isInteger(result) || result <= 0) throw new Error(`Bot 小队字段无效: ${field}`);
  return result;
}

export function mapBotTeamNodeDto(dto: BotTeamNodeDto): BotTeamNode {
  const rawBotIds = Array.isArray(dto.bot_ids) ? dto.bot_ids : dto.bot_id ? [dto.bot_id] : [];
  const botIds = rawBotIds.map((item) => requiredString(item, 'node.bot_ids'));
  if (botIds.length === 0) throw new Error('Bot 小队字段缺失: node.bot_ids');

  let completionGate = typeof dto.completion_gate === 'string' ? dto.completion_gate : '';
  if (!completionGate && dto.completion_gate && typeof dto.completion_gate === 'object') {
    const artifacts = Array.isArray(dto.completion_gate.required_artifacts)
      ? dto.completion_gate.required_artifacts
      : [];
    completionGate = artifacts
      .map((item) => {
        const artifactType = requiredString(item.artifact_type ?? item.type, 'completion_gate.artifact_type');
        const artifactState = requiredString(item.artifact_state ?? item.state, 'completion_gate.artifact_state');
        const minCount = positiveInteger(item.min_count, 'completion_gate.min_count');
        return `artifact ${artifactType} must reach state ${artifactState} with min_count ${minCount}`;
      })
      .join('; ');
  }
  return {
    code: requiredString(dto.code, 'node.code'),
    name: requiredString(dto.name, 'node.name'),
    botIds,
    approvalRequired: Boolean(dto.approval_required),
    completionGate,
  };
}


export function mapBotTeamRecordDto(dto: BotTeamRecordDto): BotTeamRecord {
  const status = stringValue(dto.status);
  if (!BOT_TEAM_STATUSES.has(status as BotTeamRecord['status'])) {
    throw new Error(`Unsupported Bot team status: ${JSON.stringify(dto.status)}`);
  }
  const publishedRevisionId = stringValue(dto.published_revision_id);
  return {
    botTeamId: requiredString(dto.bot_team_id, 'bot_team_id'),
    spaceId: positiveInteger(dto.space_id, 'space_id'),
    name: requiredString(dto.name, 'name'),
    description: stringValue(dto.description),
    status: status as BotTeamRecord['status'],
    publishedRevisionId: publishedRevisionId || undefined,
    createdBy: stringValue(dto.created_by),
    updatedBy: stringValue(dto.updated_by),
    createdAt: stringValue(dto.created_at),
    updatedAt: stringValue(dto.updated_at),
  };
}

export function mapBotTeamRevisionDto(dto: BotTeamRevisionDto): BotTeamRevision {
  const productProfileId = dto.product_profile_id === undefined || dto.product_profile_id === null
    ? undefined
    : positiveInteger(dto.product_profile_id, 'product_profile_id');
  return {
    botTeamRevisionId: requiredString(dto.bot_team_revision_id, 'bot_team_revision_id'),
    botTeamId: requiredString(dto.bot_team_id, 'bot_team_id'),
    revisionNo: positiveInteger(dto.revision_no, 'revision_no'),
    name: requiredString(dto.name, 'name'),
    description: stringValue(dto.description),
    nodes: (Array.isArray(dto.nodes) ? dto.nodes : []).map(mapBotTeamNodeDto),
    definitionHash: stringValue(dto.definition_hash),
    productProfileId,
    createdBy: stringValue(dto.created_by),
    publishedBy: stringValue(dto.published_by),
    publishedAt: stringValue(dto.published_at),
    createdAt: stringValue(dto.created_at),
    updatedAt: stringValue(dto.updated_at),
  };
}

export function mapBotTeamDetailDto(dto: BotTeamDetailDto): BotTeamDetail {
  if (!dto.bot_team) throw new Error('Bot 小队详情缺少 bot_team');
  return {
    botTeam: mapBotTeamRecordDto(dto.bot_team),
    revisions: (Array.isArray(dto.revisions) ? dto.revisions : []).map(mapBotTeamRevisionDto),
  };
}

export function mapDimaImportResultDto(dto: DimaImportResultDto): DimaImportResult {
  const status = stringValue(dto.status);
  const productRuntimeStatus = stringValue(dto.product_runtime_status);
  if (!DIMA_IMPORT_STATUSES.has(status as DimaImportResult['status'])) {
    throw new Error(`Unsupported Dima import status: ${JSON.stringify(dto.status)}`);
  }
  if (!PRODUCT_RUNTIME_STATUSES.has(productRuntimeStatus as DimaImportResult['productRuntimeStatus'])) {
    throw new Error(`Unsupported Product runtime status: ${JSON.stringify(dto.product_runtime_status)}`);
  }
  return {
    sourceWorkItemId: requiredString(dto.source_work_item_id, 'source_work_item_id'),
    status: status as DimaImportResult['status'],
    todoId: stringValue(dto.todo_id) || undefined,
    productRuntimeStatus: productRuntimeStatus as DimaImportResult['productRuntimeStatus'],
    errorCode: stringValue(dto.error_code) || undefined,
    retryable: Boolean(dto.retryable),
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

const TODO_PRODUCT_INTERACTION_KINDS = new Set<TodoProductInteractionKind>([
  'human_message',
  'stop_request',
]);

const TODO_PRODUCT_INTERACTION_STATES = new Set<TodoProductInteractionDeliveryState>([
  'sending',
  'delivery_unknown',
  'succeeded',
  'failed',
  'cancel_requested',
  'cancelled',
]);

const TODO_PRODUCT_RESPONSE_TYPES = new Set<TodoProductInteractionResponseType>([
  'DO_NOTHING',
  'OPTIMIZE',
  'RE_DO',
  'SHUTDOWN',
]);

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

function productInteractionKind(value: unknown): TodoProductInteractionKind {
  if (typeof value !== 'string' || !TODO_PRODUCT_INTERACTION_KINDS.has(value as TodoProductInteractionKind)) {
    throw new Error(`Unsupported Todo Product interaction kind: ${JSON.stringify(value)}`);
  }
  return value as TodoProductInteractionKind;
}

function productInteractionState(value: unknown): TodoProductInteractionDeliveryState {
  if (
    typeof value !== 'string' ||
    !TODO_PRODUCT_INTERACTION_STATES.has(value as TodoProductInteractionDeliveryState)
  ) {
    throw new Error(`Unsupported Todo Product interaction state: ${JSON.stringify(value)}`);
  }
  return value as TodoProductInteractionDeliveryState;
}

function optionalNumber(value: unknown): number | undefined {
  return value === undefined || value === null ? undefined : numberValue(value);
}

function productInteractionResponse(value: unknown): TodoProductInteraction['response'] {
  const record = recordValue(value);
  if (!Object.keys(record).length) return undefined;
  const type = stringValue(record.type);
  if (!TODO_PRODUCT_RESPONSE_TYPES.has(type as TodoProductInteractionResponseType)) {
    throw new Error(`Unsupported Todo Product interaction response: ${JSON.stringify(record.type)}`);
  }
  return {
    type: type as TodoProductInteractionResponseType,
    message: stringValue(record.message),
  };
}

export function mapTodoProductInteractionDto(dto: TodoProductInteractionDto): TodoProductInteraction {
  const interactionId = stringValue(dto.interaction_id);
  const todoId = stringValue(dto.todo_id);
  const actorId = stringValue(dto.actor_id);
  const requestMessage = stringValue(dto.request_message);
  const createdAt = stringValue(dto.created_at);
  const updatedAt = stringValue(dto.updated_at);
  if (!interactionId || !todoId || !actorId || !requestMessage || !createdAt || !updatedAt) {
    throw new Error('Todo Product interaction identity fields are missing');
  }
  const productColletId = numberValue(dto.product_collet_id);
  if (productColletId <= 0) throw new Error('Todo Product interaction identity fields are missing');
  return {
    interactionId,
    todoId,
    kind: productInteractionKind(dto.kind),
    actorId,
    actorName: optionalString(dto.actor_name),
    requestMessage,
    deliveryState: productInteractionState(dto.delivery_state),
    productColletId,
    colletRolloutId: optionalNumber(dto.collet_rollout_id),
    expectedRevision: optionalNumber(dto.expected_revision),
    targetInteractionId: optionalString(dto.target_interaction_id),
    response: productInteractionResponse(dto.response),
    failureCode: optionalString(dto.failure_code),
    createdAt,
    updatedAt,
  };
}

export function mapTodoProductInteractionPageDto(
  dto: TodoProductInteractionPageDto,
): TodoProductInteractionPage {
  return {
    items: (Array.isArray(dto.items) ? dto.items : []).map(mapTodoProductInteractionDto),
    canSend: Boolean(dto.can_send),
    blockingInteractionId: optionalString(dto.blocking_interaction_id),
  };
}
