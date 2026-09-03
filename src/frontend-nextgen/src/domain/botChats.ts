export type BotChatMatchMode = 'exact' | 'contains';
export type BotChatRelationScope = 'session' | 'task' | 'group';

export interface BotChatFilters {
  traceId: string;
  sessionId: string;
  sessionKey: string;
  keyword: string;
  bizScene: string;
  bizTaskId: string;
  groupId: string;
  fromDate: string;
  toDate: string;
}

export interface BotChatSummary {
  id: string;
  botId?: string;
  botName?: string;
  timestamp: string;
  sessionId?: string;
  sessionKey?: string;
  name: string;
  input?: unknown;
  outputPreview?: string;
  bizScene?: string;
  bizTaskId?: string;
  groupId?: string;
  model?: string;
  status: string;
  latencyMs: number;
  totalTokens: number;
  totalCost: number;
}

export interface BotChatObservation {
  id: string;
  type: string;
  name: string;
  modelName?: string;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
  latencyMs: number;
  totalTokens: number;
  totalCost: number;
  children: BotChatObservation[];
}

export interface BotChatDetail extends BotChatSummary {
  output?: unknown;
  metadata?: Record<string, unknown>;
  observations: BotChatObservation[];
}

export interface BotChatPage {
  items: BotChatSummary[];
  total: number;
  page: number;
  limit: number;
  hasMore: boolean;
}

export interface BotChatContext {
  botId: string;
  botName: string;
  userId: string;
  ownerId?: string;
}

export interface BotChatDetailSelection {
  traceId: string;
  sessionId?: string;
  botId?: string;
}
