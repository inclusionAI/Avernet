import { getIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import {
  getPrivateChatSession,
  PrivateChatSessionBusinessError,
  type PrivateChatSession,
  type PrivateChatSessionConnection,
} from '@/services/backendApi/privateChat/privateChatController';
import { getPrivateSessionMessages } from '@/services/backendApi/privateChat/privateSessionController';
import { OpenClawProvider, type ConnectionStatusEvent } from '@tc-chat/adapters';
import type { AixContext, ChatMessage, ChatProvider } from '@tc-chat/core';
import { mapPrivateHistoryMessages } from './workspaceMessageMapper';
import type { SupportChatState } from './workspaceModel';
export { mapPrivateHistoryMessages } from './workspaceMessageMapper';

export const TEAMCLAW_SUPPORT_BOT = {
  botId: '20260402_mnpvqm6v',
  ownerId: '103892',
  targetId: 'teamclaw-support',
} as const;

export interface SupportChatRequest {
  content: string;
  targetId: string;
  panelContext?: AixContext;
}

interface SupportProviderOptions {
  pollIntervalMs?: number;
  pollTimeoutMs?: number;
  getSession?: typeof getPrivateChatSession;
  getMessages?: typeof getPrivateSessionMessages;
  createOpenClawProvider?: (config: ConstructorParameters<typeof OpenClawProvider>[0]) => OpenClawProvider;
  getIamToken?: typeof getIamToken;
}

const DEFAULT_POLL_INTERVAL_MS = 3_000;
const DEFAULT_POLL_TIMEOUT_MS = 10 * 60_000;
const NO_PERMISSION_ERROR_CODE = 5002;

function wait(ms: number) {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });
}

function normalizeConnectionType(type: string): 'local' | 'remote' | 'desktop' {
  return type === 'local' || type === 'desktop' ? type : 'remote';
}

export function resolvePrivateWebsocketPath(engineType?: string): string {
  const engine = engineType?.trim().toLowerCase() || 'openclaw';
  return engine === 'aicoding' ? '/api/ws' : `/api/${engine}/ws`;
}

export function buildPrivateWebsocketUrl(
  connection: PrivateChatSessionConnection,
  locationLike?: Pick<Location, 'protocol' | 'host'>,
): string {
  const path = resolvePrivateWebsocketPath(connection.engine_type);
  const connectionType = normalizeConnectionType(connection.type);
  if (connectionType === 'local' || connectionType === 'desktop') {
    return `ws://${connection.target}${path}`;
  }

  const location = locationLike ?? (typeof window !== 'undefined' ? window.location : undefined);
  const protocol = location?.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = location?.host || 'localhost';
  return `${protocol}//${host}/proxypass/${connection.target}${path}`;
}

interface MutableOpenClawProvider {
  config?: { xIAMToken?: string };
  parser?: { config?: { xIAMToken?: string } };
  transport?: { openClawConfig?: { xIAMToken?: string } };
}

function updateProviderIamToken(provider: OpenClawProvider, token: string) {
  const mutable = provider as unknown as MutableOpenClawProvider;
  if (mutable.config) mutable.config.xIAMToken = token;
  if (mutable.parser?.config) mutable.parser.config.xIAMToken = token;
  if (mutable.transport?.openClawConfig) mutable.transport.openClawConfig.xIAMToken = token;
}

export class TeamClawSupportProvider implements ChatProvider<SupportChatRequest> {
  public onMessage?: (message: ChatMessage) => void;
  public onComplete?: (messages: ChatMessage[]) => void;
  public onError?: (error: Error) => void;

  private readonly options: Required<
    Pick<
      SupportProviderOptions,
      'pollIntervalMs' | 'pollTimeoutMs' | 'getSession' | 'getMessages' | 'createOpenClawProvider' | 'getIamToken'
    >
  >;
  private inner: OpenClawProvider | null = null;
  private session: PrivateChatSession | null = null;
  private initializationPromise: Promise<PrivateChatSession> | null = null;
  private iamToken = '';
  private state: SupportChatState = { phase: 'idle', error: null };
  private stateListeners = new Set<(state: SupportChatState) => void>();
  private connectionListeners = new Set<(event: ConnectionStatusEvent) => void>();
  private unsubscribeInnerConnection?: () => void;

  constructor(options: SupportProviderOptions = {}) {
    this.options = {
      pollIntervalMs: options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS,
      pollTimeoutMs: options.pollTimeoutMs ?? DEFAULT_POLL_TIMEOUT_MS,
      getSession: options.getSession ?? getPrivateChatSession,
      getMessages: options.getMessages ?? getPrivateSessionMessages,
      createOpenClawProvider: options.createOpenClawProvider ?? ((config) => new OpenClawProvider(config)),
      getIamToken: options.getIamToken ?? getIamToken,
    };
  }

  get isConnected() {
    return this.inner?.isConnected ?? false;
  }

  get supportState() {
    return this.state;
  }

  subscribeToSupportState(listener: (state: SupportChatState) => void) {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => this.stateListeners.delete(listener);
  }

  subscribeToConnectionStatus(listener: (event: ConnectionStatusEvent) => void) {
    this.connectionListeners.add(listener);
    return () => this.connectionListeners.delete(listener);
  }

  async connect() {
    this.emitConnection({ status: 'connecting', retryCount: 0 });
    try {
      await this.ensureInitialized();
      await this.inner?.connect();
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.setState({ phase: 'error', error: normalized.message });
      this.emitConnection({ status: 'error', retryCount: 0, error: normalized });
      throw normalized;
    }
  }

  disconnect() {
    this.inner?.disconnect();
    this.emitConnection({ status: 'disconnected', retryCount: 0 });
  }

  async request(params: SupportChatRequest, messageId?: string) {
    if (params.targetId !== TEAMCLAW_SUPPORT_BOT.targetId) {
      throw new Error('当前会话尚未接入在线对话能力');
    }
    const session = await this.ensureInitialized();
    const iamToken = await this.options.getIamToken();
    this.iamToken = iamToken;
    if (this.inner) updateProviderIamToken(this.inner, iamToken);
    if (!this.inner?.isConnected) await this.connect();
    await this.inner?.request(
      { query: params.content, sessionKey: session.session_key, panelContext: params.panelContext },
      messageId,
    );
  }

  abort() {
    this.inner?.abort();
  }

  async loadHistory(): Promise<ChatMessage[]> {
    this.setState({ phase: 'loading-history', error: null });
    try {
      const session = await this.ensureInitialized();
      const connection = this.getConnection(session);
      const messages = await this.options.getMessages(session.session_key, connection, {
        limit: 1000,
        offset: 0,
      });
      this.setState({ phase: this.isConnected ? 'ready' : 'idle', error: null });
      return mapPrivateHistoryMessages(messages);
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.setState({ phase: 'error', error: normalized.message });
      throw normalized;
    }
  }

  private ensureInitialized(): Promise<PrivateChatSession> {
    if (this.session && this.inner) return Promise.resolve(this.session);
    if (this.initializationPromise) return this.initializationPromise;

    this.initializationPromise = this.initialize().catch((error) => {
      this.initializationPromise = null;
      throw error;
    });
    return this.initializationPromise;
  }

  private async initialize(): Promise<PrivateChatSession> {
    const deadline = Date.now() + this.options.pollTimeoutMs;
    let lastError: Error | null = null;
    this.setState({ phase: 'preparing', error: null });
    this.iamToken = await this.options.getIamToken();

    while (Date.now() <= deadline) {
      try {
        const session = await this.options.getSession(TEAMCLAW_SUPPORT_BOT.botId, TEAMCLAW_SUPPORT_BOT.ownerId);
        const connection = session.collection ?? session.connection;
        if (connection?.target && connection.type) {
          this.session = session;
          this.createInnerProvider(session, connection);
          this.setState({ phase: 'idle', error: null });
          return session;
        }
        if (!session.need_poll) throw new Error('客服连接信息为空，请稍后重试');
      } catch (error) {
        const normalized = error instanceof Error ? error : new Error(String(error));
        lastError = normalized;
        if (error instanceof PrivateChatSessionBusinessError && error.errorCode === NO_PERMISSION_ERROR_CODE) {
          throw normalized;
        }
      }

      if (Date.now() + this.options.pollIntervalMs > deadline) break;
      await wait(this.options.pollIntervalMs);
    }

    throw new Error(lastError?.message || '客服对话环境准备超时，请稍后重试');
  }

  private createInnerProvider(session: PrivateChatSession, connection: PrivateChatSessionConnection) {
    const inner = this.options.createOpenClawProvider({
      url: buildPrivateWebsocketUrl(connection),
      xProxypassToken: connection.token,
      xIAMToken: this.iamToken,
      sessionKey: session.session_key,
      immediateConnect: true,
      reconnectAttempts: 3,
      heartbeatInterval: 30_000,
      heartbeatTimeout: 5 * 60_000,
      connectionTimeout: 10_000,
      enableThinkingTag: true,
      fallbackMessage: '请求失败，请稍后重试',
      credentialProvider: async () => {
        const refreshed = await this.options.getSession(TEAMCLAW_SUPPORT_BOT.botId, TEAMCLAW_SUPPORT_BOT.ownerId);
        const [refreshedConnection, refreshedIamToken] = await Promise.all([
          Promise.resolve(this.getConnection(refreshed)),
          this.options.getIamToken(),
        ]);
        this.session = refreshed;
        this.iamToken = refreshedIamToken;
        return { xProxypassToken: refreshedConnection.token, xIAMToken: refreshedIamToken };
      },
    });

    inner.onMessage = (message) => this.onMessage?.(message);
    inner.onComplete = (messages) => this.onComplete?.(messages);
    inner.onError = (error) => this.onError?.(error);
    this.unsubscribeInnerConnection?.();
    this.unsubscribeInnerConnection = inner.subscribeToConnectionStatus((event) => {
      if (event.status === 'connected') this.setState({ phase: 'ready', error: null });
      if (event.status === 'error') this.setState({ phase: 'error', error: event.error?.message || '连接失败' });
      this.emitConnection(event);
    });
    this.inner = inner;
  }

  private getConnection(session: PrivateChatSession): PrivateChatSessionConnection {
    const connection = session.collection ?? session.connection;
    if (!connection) throw new Error('客服连接信息为空，请稍后重试');
    return connection;
  }

  private setState(state: SupportChatState) {
    this.state = state;
    this.stateListeners.forEach((listener) => listener(state));
  }

  private emitConnection(event: ConnectionStatusEvent) {
    this.connectionListeners.forEach((listener) => listener(event));
  }
}
