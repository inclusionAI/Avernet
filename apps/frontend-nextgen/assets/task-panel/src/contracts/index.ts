import type { TaskArtifactView } from '../panel/types';
export interface ConversationRef {
  sessionId: string;
  kind: 'bot' | 'group' | 'friend' | 'unknown';
  botId?: string | null;
  groupId?: string | null;
}
export interface ConversationTarget extends ConversationRef { startTime?: number | null; endTime?: number | null; }
export type NavigationOutcome = { status: 'ready' } | { status: 'denied' | 'unavailable' | 'cancelled'; reason: string };
export interface HistoryMessage { id: string; content: string; timestamp: number; }
export interface HistoryPage { messages: readonly HistoryMessage[]; nextCursor: string | null; }
export interface HistoryReader {
  readPage(conversation: ConversationRef, cursor: string | null, signal: AbortSignal): Promise<HistoryPage>;
}
export interface TaskPanelTransport { requestJson(path: string, signal: AbortSignal): Promise<unknown>; }
export interface ArtifactOwner { taskId: string; ownerBotId: string; ownerUserId: string; }
export interface TaskArtifactReader {
  contentUrl?(artifact: TaskArtifactView): string | null;
  readContent(artifact: TaskArtifactView, owner: ArtifactOwner, signal: AbortSignal): Promise<Blob>;
  download(artifact: TaskArtifactView, owner: ArtifactOwner, signal: AbortSignal): Promise<void>;
}
export interface TaskPanelDeclaration {
  id: string; type: string; title: string; closable: boolean; defaultWidth?: number | `${number}px` | `${number}%`;
  params: Record<string, unknown> & { taskId: string };
}
export type OpenSource = 'explicit' | 'message' | 'recovery';
export interface TaskPanelInteraction {
  source: { type: 'panel'; target: string }; description: string;
  action: { verb: string; subject: string; params: Record<string, unknown> };
  snapshot?: Record<string, unknown>;
}
export interface TaskPanelHost {
  getViewer(): { userId: string } | null;
  history: HistoryReader; transport: TaskPanelTransport; artifacts: TaskArtifactReader;
  getConversation(): ConversationRef | null;
  navigate(target: ConversationTarget, signal: AbortSignal): Promise<NavigationOutcome>;
  captureOrigin(): unknown;
  restoreOrigin(origin: unknown, signal: AbortSignal): Promise<NavigationOutcome>;
  present(tab: TaskPanelDeclaration, source: OpenSource): void;
  preservePanel(preserve: boolean): void;
  recordInteraction(interaction: TaskPanelInteraction): void;
  taskApiBase?: string;
}
export type RecoveryState =
  | { status: 'idle' | 'searching' | 'not-found' | 'opened' }
  | { status: 'incomplete'; pagesRead: number; resumeCursor: string }
  | { status: 'error'; reason: string };
