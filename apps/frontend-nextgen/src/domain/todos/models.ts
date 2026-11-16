/** 待办工作台领域模型。组件只依赖这些领域类型，不依赖后端 DTO。 */
export type TodoStatus = 'pending' | 'in_progress' | 'pending_acceptance' | 'completed';

export type AcceptanceVerdict = 'approved' | 'rejected';

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
}

export interface CreateTodoInteractionInput {
  message: string;
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
