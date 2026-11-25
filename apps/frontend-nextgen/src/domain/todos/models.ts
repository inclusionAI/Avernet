/** 待办工作台领域模型。组件只依赖这些领域类型，不依赖后端 DTO。 */
export type TodoStatus = 'pending' | 'in_progress' | 'pending_acceptance' | 'completed';

export type AcceptanceVerdict = 'approved' | 'rejected';

export type TodoSourceType = 'manual' | 'dima';
export type TodoSourceWorkItemCategory = 'req' | 'task' | 'bug';

export interface TodoSourceProject {
  id: string;
  name: string;
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
}

export interface TodoCreateSource {
  type: 'dima';
  containerId: string;
  itemId: string;
  url?: string;
  updatedAt?: string;
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
  templateId?: string;
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
  todoExecutionId?: string;
  stageInstanceId?: string;
  generation?: number;
  isLatest: boolean;
  replyable: boolean;
  replyDisabledReason?: string;
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
  targetExecutionId?: string;
  targetStageInstanceId?: string;
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

export interface TodoBotOption {
  botId: string;
  botName: string;
}
