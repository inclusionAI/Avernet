/** 待办工作台领域模型。组件只依赖这些领域类型，不依赖后端 DTO。 */
export type TodoStatus = 'draft' | 'in_progress' | 'pending_acceptance' | 'completed';

export type AcceptanceVerdict = 'approved' | 'rejected';

export interface TodoArtifact {
  artifactId?: string;
  name: string;
  url: string;
}

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
  artifacts: TodoArtifact[];
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

export interface TodoAcceptanceInput {
  verdict: AcceptanceVerdict;
  message?: string;
}

export interface TodoBotOption {
  botId: string;
  botName: string;
}
