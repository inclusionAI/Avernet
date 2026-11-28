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
export type TodoExecutorType = 'bot' | 'bot_team';
export type BotTeamStatus = 'active' | 'archived';

export interface TodoOwner {
  userId: string;
  userName: string;
}

export interface TodoExecutor {
  type: TodoExecutorType;
  id: string;
  revisionId?: string;
}

export interface TodoExecutorOption extends TodoExecutor {
  name: string;
  description?: string;
}

export interface BotTeamNode {
  code: string;
  name: string;
  botIds: string[];
  approvalRequired: boolean;
  completionGate: string;
}

export interface BotTeamRecord {
  botTeamId: string;
  spaceId: number;
  name: string;
  description: string;
  status: BotTeamStatus;
  publishedRevisionId?: string;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface BotTeamRevision {
  botTeamRevisionId: string;
  botTeamId: string;
  revisionNo: number;
  name: string;
  description: string;
  nodes: BotTeamNode[];
  definitionHash: string;
  productProfileId?: number;
  createdBy: string;
  publishedBy: string;
  publishedAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface BotTeamDetail {
  botTeam: BotTeamRecord;
  revisions: BotTeamRevision[];
}

export interface SaveBotTeamInput {
  spaceId?: number;
  name: string;
  description: string;
  nodes: BotTeamNode[];
}

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
  schedulerBotId?: string;
  triggerIntervalSeconds: number;
  cronExpression: string;
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
  schedulerBotId: string;
  triggerIntervalSeconds: number;
  cronExpression: string;
}

export interface UpdateTodoAutoImportRuleInput {
  sourceContainerIds?: string[];
  filterText?: string;
  botId?: string;
  schedulerBotId?: string;
  triggerIntervalSeconds?: number;
  cronExpression?: string;
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
  ownerId?: string;
  ownerName?: string;
  templateId?: string;
  templateVersionId?: string;
  templateName?: string;
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
  executor: TodoExecutor;
  owner: TodoOwner;
  source?: TodoCreateSource;
}

export interface ImportDimaTodoItemInput {
  title: string;
  description: string;
  source: TodoCreateSource;
}

export interface ImportDimaTodosInput {
  spaceId: number;
  executor: TodoExecutor;
  owner: TodoOwner;
  items: ImportDimaTodoItemInput[];
}

export type DimaImportStatus = 'imported' | 'duplicate' | 'failed';
export type ProductRuntimeStatus =
  | 'ready'
  | 'retryable_failure'
  | 'not_applicable'
  | 'binding_conflict'
  | 'invalid_binding';

export interface DimaImportResult {
  sourceWorkItemId: string;
  status: DimaImportStatus;
  todoId?: string;
  productRuntimeStatus: ProductRuntimeStatus;
  errorCode?: string;
  retryable: boolean;
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
  ownerId?: string;
}

export type TodoProductInteractionKind = 'human_message' | 'stop_request';

export type TodoProductInteractionDeliveryState =
  | 'sending'
  | 'delivery_unknown'
  | 'succeeded'
  | 'failed'
  | 'cancel_requested'
  | 'cancelled';

export type TodoProductInteractionResponseType = 'DO_NOTHING' | 'OPTIMIZE' | 'RE_DO' | 'SHUTDOWN';

export interface TodoProductInteractionResponse {
  type: TodoProductInteractionResponseType;
  message: string;
}

export interface TodoProductInteraction {
  interactionId: string;
  todoId: string;
  kind: TodoProductInteractionKind;
  actorId: string;
  actorName?: string;
  requestMessage: string;
  deliveryState: TodoProductInteractionDeliveryState;
  productColletId: number;
  colletRolloutId?: number;
  expectedRevision?: number;
  targetInteractionId?: string;
  response?: TodoProductInteractionResponse;
  failureCode?: string;
  createdAt: string;
  updatedAt: string;
}

export interface TodoProductInteractionPage {
  items: TodoProductInteraction[];
  canSend: boolean;
  blockingInteractionId?: string;
}

export interface CreateTodoProductInteractionInput {
  message: string;
  replyToInteractionId?: string;
}
