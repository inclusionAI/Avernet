/** Optional v1 push-only presentation protocol. No polling or runtime commands. */
export interface SessionStatusPresentation {
  code: string;
  label: string;
  tone: 'neutral' | 'info' | 'warning' | 'success' | 'danger';
}
export type SessionStatusEvent = {
  version: 1;
  contextId: string;
  botId: string;
  sequence: number;
} & (
  | { type: 'reset' }
  | {
      type: 'patch';
      /** True marks the first successful status-query snapshot; regular v1 patches explicitly use false. */
      snapshot?: boolean;
      items: Array<{ sessionId: string; status: SessionStatusPresentation | null }>;
    }
);
export interface SessionStatusHostProps {
  sessionStatusContextId?: string;
  onSessionStatusEvent?: (event: SessionStatusEvent) => void;
}
