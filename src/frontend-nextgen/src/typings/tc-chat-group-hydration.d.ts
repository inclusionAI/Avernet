import '@tc-chat/adapters';
import type { ChatMessage } from '@tc-chat/core';

/**
 * Temporary declaration bridge for paired local SDK development.
 * Remove it after Teamclaw consumes an internal tcchat-adapters dev version
 * whose published declarations include these public APIs.
 */
declare module '@tc-chat/adapters' {
  interface GroupChatProviderConfig {
    sessionId?: string;
  }

  interface GroupChatInput {
    sessionId?: string;
  }

  interface GroupChatProvider {
    connect(params?: { groupId?: string; sessionId?: string }): Promise<void>;
    hydrateRun(message: ChatMessage): ChatMessage;
    beginHistoryHydration(): void;
    enterLiveMode(): void;
  }
}
