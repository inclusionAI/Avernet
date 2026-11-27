/** 待办工作台领域模型。组件只依赖这些领域类型，不依赖后端 DTO。 */
export type TodoStatus =
  | 'not_started'
  | 'in_progress'
  | 'interrupted'
  | 'pending_action'
  | 'completed'
  | 'cancelled';

export type AcceptanceVerdict = 'approved' | 'rejected';

export type TodoSourceType = 'manual' | 'dima';
export type TodoSourceWorkItemCategory = 'req' | 'task' | 'bug';
export type TodoSourceMetadata = Record<string, string>;
export type TodoAutoImportRuleStatus = 'enabled' | 'disabled';

/** Todo 工作台从 Task Engine 读取的最小空间视图。 */
export interface TodoSpace {
  spaceId: number;
  spaceName: string;
  spaceType: 'TEAM' | 'PERSONAL' | 'UNKNOWN';
}

export interface TodoSourceProject {
  id: string;
  name: string;
  kind: 'workspace' | 'project';
}

export interface TodoSourceWorkItem {
  id: string;
  projectId: string;
  category: TodoSourceWorkItemCategory;
  title: string;
  status?: string;
  updatedAt?: string;
}

export interface TodoSourceDraft {
  sourceType: 'dima';
  projectId: string;
  workItemId: string;
  category: TodoSourceWorkItemCategory;
  title: string;
  description: string;
  sourceUrl?: string;
  sourceUpdatedAt?: string;
  metadata: TodoSourceMetadata;
}

export interface TodoCreateSource {
  type: 'dima';
  containerId: string;
  itemId: string;
  url?: string;
  updatedAt?: string;
  metadata: TodoSourceMetadata;
}

export interface TodoAutoImportRule {
  ruleId: string;
  spaceId: number;
  sourceContainerIds: string[];
  filterText: string;
  botId: string;
  status: TodoAutoImportRuleStatus;
  cursorUpdatedAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface CreateTodoAutoImportRuleInput {
  spaceId: number;
  sourceContainerIds: string[];
  filterText: string;
  botId: string;
}

export interface UpdateTodoAutoImportRuleInput {
  sourceContainerIds?: string[];
  filterText?: string;
  botId?: string;
  status?: TodoAutoImportRuleStatus;
}

export interface TodoEventArtifact {
  artifactId?: string;
  name: string;
  url: string;
}

interface TodoArtifactBase {
  artifactId: string;
  todoId: string;
  executionId: string;
  artifactName: string;
  createdAt: string;
}

export type TodoArtifact =
  | (TodoArtifactBase & {
      kind: 'text';
      content: {
        text: string;
        isFinalArtifact: boolean;
      };
    })
  | (TodoArtifactBase & {
      kind: 'file';
      content: {
        fileName: string;
        fileType: string;
        size: number;
        isFinalArtifact: boolean;
      };
    })
  | (TodoArtifactBase & {
      kind: 'url_link';
      content: {
        url: string;
        type?: string;
        isFinalArtifact: boolean;
      };
    })
  | (TodoArtifactBase & {
      kind: 'code';
      content: {
        codeBaseName: string;
        codeBaseAddress: string;
        prLink?: string;
        prDesc?: string;
        isFinalArtifact: boolean;
      };
    });

export interface TodoRecord {
  todoId: string;
  spaceId: number;
  title: string;
  description: string;
  botId: string;
  botName?: string;
  status: TodoStatus;
  collabContextId?: string;
  createdAt: string;
  updatedAt: string;
}

export type TodoActorType = 'human' | 'bot' | string;

export interface TodoEvent {
  eventId: string;
  eventSeq: number;
  eventType: string;
  actorId: string;
  actorType: TodoActorType;
  content: string;
  executionId: string;
  contextVersion?: number;
  status?: string;
  createdAt: string;
  artifacts: TodoEventArtifact[];
  payload: Record<string, unknown>;
}

export interface TodoListQuery {
  spaceId: number;
  keyword?: string;
  status?: TodoStatus;
  botId?: string;
  page: number;
  pageSize: number;
}

export interface CreateTodoInput {
  spaceId: number;
  title: string;
  description: string;
  botId: string;
  source?: TodoCreateSource;
}

export interface CreateTodoInteractionInput {
  message: string;
  replyToEventId?: string;
  mentions?: TodoInteractionMention[];
}

export interface TodoInteractionMention {
  actorType: 'bot';
  actorId: string;
}

export interface TodoAcceptanceTarget {
  kind: 'event' | 'artifact';
  id: string;
  floor: number;
}

export interface TodoAcceptanceInput {
  decisionId: string;
  verdict: AcceptanceVerdict;
  message?: string;
  targets: TodoAcceptanceTarget[];
}

export type TodoProgressRuntimeKind = 'product_collet' | 'product_runtime_pending' | 'legacy_context' | 'invalid_binding';

export type TodoProgressStageStatus =
  | 'not_started'
  | 'in_progress'
  | 'pending_action'
  | 'interrupted'
  | 'completed'
  | 'cancelled'
  | 'unknown';

export interface TodoProgressStep {
  stepId: string;
  name: string;
  status: TodoProgressStageStatus;
  detail: string;
  occurredAt?: string;
}

export interface TodoProgressOutput {
  artifactId?: string;
  kind: string;
  name: string;
  summary: string;
}

export interface TodoProgressBlocker {
  reason?: string;
  requestedAction?: string;
  errorCode?: string;
}

export interface TodoStageProgress {
  stageInstanceId: string;
  code: string;
  name: string;
  order: number;
  kind: string;
  status: TodoProgressStageStatus;
  ownerUserId: string;
  ownerUserName: string;
  executorSubjectId: string;
  executorSubjectName: string;
  summary: string;
  currentStep?: string;
  steps: TodoProgressStep[];
  outputs: TodoProgressOutput[];
  blocker?: TodoProgressBlocker;
  attemptCount: number;
  detailsLoaded: boolean;
  messagesComplete?: boolean;
  ignoredMessageCount: number;
  updatedAt?: string;
}

export interface TodoProgress {
  todoId: string;
  status: TodoStatus;
  runtimeKind: TodoProgressRuntimeKind;
  runtimeUnavailable: boolean;
  runtimeErrorCode?: string;
  currentStageCode?: string;
  rolloutRevision?: number;
  stopped?: boolean;
  stages: TodoStageProgress[];
}

export interface TodoBotOption {
  botId: string;
  botName: string;
}
