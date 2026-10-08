import { getBotConnection } from '@/services/backendApi/bots/privateBotSessionController';
import { getBotIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import { OpenClawProvider, type ConnectionStatusEvent, type OpenClawProviderConfig } from '@tc-chat/adapters';
import type { ChatMessage, ChatProvider, PromptFileRef, ResourceReference } from '@tc-chat/core';
import { installCompleteFallback } from './botChatCompleteFallback';
import {
  BOT_MESSAGE_PAGE_SIZE,
  botSessionService,
  resolveUserId,
  withFriendBotRequestParams,
  type ChatBotView,
} from './botSessionService';

export interface BotChatRequest {
  content: string;
  sessionId: string;
  resourceReferences?: ResourceReference[];
  promptFileRefs?: PromptFileRef[];
}
export interface BotChatState {
  phase: 'idle' | 'preparing' | 'loading-history' | 'ready' | 'error';
  error: string | null;
}

interface BotChatProviderOptions {
  bot: ChatBotView;
  userId: string;
  sessionId: string;
  /** 注入 SDK Provider 构造(测试 stub);缺省真实 SDK。 */
  createSdkProvider?: (config: OpenClawProviderConfig) => OpenClawProvider;
  /** 注入 IAM token 拉取(测试 stub);缺省走 /openapi/v1/bots/{bot_id}/iam-token。 */
  getIamToken?: typeof getBotIamToken;
}

/** SDK OpenClawProvider 未公开 updateIAMToken,直接写入其内部 config/parser/transport 三处引用,
 *  保证每次 req(chat.send)帧与 connect 帧都携带最新 x-iam-token(与 supportProvider 同款)。 */
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

/** Bot 单聊 Provider——封装 SDK OpenClawProvider 生命周期:
 *  GET /openapi/v1/bots/{bot_id}/connection → data.sockets.find(chat).url 取其 path+query(连接凭证内含于
 *  query),host 经 buildWsUrlFromRelative 按部署环境重建(见 ae52fe4:部署态走 tern cors proxy 直连网关),
 *  sessionKey = session_id。连接凭证(URL token)与请求身份凭证(x-iam-token)是两套:前者管 WS 握手,
 *  后者管每个 chat.send 帧的身份鉴权——初始化与每次发送前都需经 getIamToken() 拉取并注入 SDK,
 *  否则 WS 能连上但发送会被服务端拒(身份不明)。WS 协议与客服(supportProvider)示例一致。 */
export class BotChatProvider implements ChatProvider<BotChatRequest> {
  public onMessage?: (message: ChatMessage) => void;
  public onComplete?: (messages: ChatMessage[]) => void;
  public onError?: (error: Error) => void;

  private readonly options: BotChatProviderOptions;
  private inner: OpenClawProvider | null = null;
  private initializePromise: Promise<OpenClawProvider> | null = null;
  private connectionPromise: Promise<void> | null = null;
  /** disconnect() 递增；异步 connect 在每个 await 后校验，过期连接静默清理。 */
  private connectionEpoch = 0;
  private readonly getIamTokenFn: typeof getBotIamToken;
  private iamToken = '';
  private directDesktop = false;
  private desktopReconnectTimer?: ReturnType<typeof setTimeout>;
  private manuallyDisconnected = false;
  private connectionListeners = new Set<(event: ConnectionStatusEvent) => void>();
  private stateListeners = new Set<(state: BotChatState) => void>();
  private unsubscribeInnerConnection?: () => void;
  private state: BotChatState = { phase: 'idle', error: null };
  private teardownFallback?: () => void;
  private historyPage = 0;
  private loadedHistoryRawCount = 0;
  private historyTotal = 0;
  private loadingMoreHistory = false;

  constructor(options: BotChatProviderOptions) {
    this.options = options;
    this.getIamTokenFn = options.getIamToken ?? getBotIamToken;
  }

  get isConnected(): boolean {
    return this.inner?.isConnected ?? false;
  }
  get supportState(): BotChatState {
    return this.state;
  }
  get hasMoreHistory(): boolean {
    return this.loadedHistoryRawCount < this.historyTotal;
  }
  get isLoadingMoreHistory(): boolean {
    return this.loadingMoreHistory;
  }

  subscribeToSupportState(listener: (state: BotChatState) => void): () => void {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => {
      this.stateListeners.delete(listener);
    };
  }
  subscribeToConnectionStatus(listener: (event: ConnectionStatusEvent) => void): () => void {
    this.connectionListeners.add(listener);
    return () => {
      this.connectionListeners.delete(listener);
    };
  }

  private async getChatUrl(): Promise<string> {
    const resp = await getBotConnection(
      this.options.bot.realBotId,
      withFriendBotRequestParams(this.options.bot, this.options.userId, {
        user_id: resolveUserId(this.options.userId),
        owner_id: this.options.bot.ownerId,
        ...(this.options.bot.isFriendBot ? { session_id: this.options.sessionId } : {}),
      }),
    );
    const socket = resp.data?.sockets?.find((s) => s.kind === 'chat');
    if (!socket?.url) throw new Error('Bot 连接信息为空,请稍后重试');
    this.directDesktop = resp.data?.transport_mode === 'direct';
    return socket.url;
  }

  private ensureInitialized(): Promise<OpenClawProvider> {
    if (this.inner) return Promise.resolve(this.inner);
    if (this.initializePromise) return this.initializePromise;
    this.initializePromise = this.initialize().catch((error) => {
      this.initializePromise = null;
      throw error;
    });
    return this.initializePromise;
  }

  private async initialize(): Promise<OpenClawProvider> {
    this.setState({ phase: 'preparing', error: null });
    const url = await this.getChatUrl();
    // 连接凭证(URL query token,管 WS 握手)与请求身份凭证(x-iam-token,管 chat.send 帧)是两套:
    // URL 已内含握手凭证;此处须单独拉取 IAM token 并注入 SDK,否则发送会被服务端拒(身份不明)。
    this.iamToken = this.directDesktop
      ? ''
      : await this.getIamTokenFn(
          this.options.bot.realBotId,
          resolveUserId(this.options.userId),
          this.options.bot.ownerId,
          this.options.bot.runtimeStage ?? 'online',
        );
    const factory = this.options.createSdkProvider ?? ((cfg) => new OpenClawProvider(cfg));
    const inner = factory({
      url,
      sessionKey: this.options.sessionId,
      xIAMToken: this.iamToken,
      immediateConnect: true,
      reconnectAttempts: this.directDesktop ? 0 : 3,
      heartbeatInterval: 30_000,
      heartbeatTimeout: 5 * 60_000,
      connectionTimeout: 10_000,
      enableThinkingTag: true,
      fallbackMessage: '请求失败,请稍后重试',
      // SDK 重连前刷新 IAM token(连接 URL 的 token 在 expires_at 内仍有效,SDK 复用原 url;
      // 需换签走 reconnect() 重走 initialize() 重新 getBotConnection)。仅返回 xIAMToken,
      // 不返回 xProxypassToken——避免 SDK 给内含 token 的 url 再次追加 x-proxypass-token。
      credentialProvider: async () => {
        if (this.directDesktop) return {};
        const refreshed = await this.getIamTokenFn(
          this.options.bot.realBotId,
          resolveUserId(this.options.userId),
          this.options.bot.ownerId,
          this.options.bot.runtimeStage ?? 'online',
        );
        this.iamToken = refreshed;
        return { xIAMToken: refreshed };
      },
    });

    inner.onMessage = (message) => this.onMessage?.(message);
    inner.onComplete = (messages) => this.onComplete?.(messages);
    inner.onError = (error) => this.onError?.(error);
    this.teardownFallback = installCompleteFallback(inner, (msgs) => this.onComplete?.(msgs));
    this.unsubscribeInnerConnection?.();
    this.unsubscribeInnerConnection = inner.subscribeToConnectionStatus((event) => {
      if (event.status === 'connected') this.setState({ phase: 'ready', error: null });
      if (event.status === 'error') this.setState({ phase: 'error', error: event.error?.message || '连接失败' });
      if (this.directDesktop && (event.status === 'error' || event.status === 'disconnected'))
        this.scheduleDesktopReconnect();
      this.emitConnection(event);
    });
    this.inner = inner;
    return inner;
  }

  connect(): Promise<void> {
    this.manuallyDisconnected = false;
    if (this.connectionPromise) return this.connectionPromise;
    const epoch = this.connectionEpoch;
    let trackedPromise!: Promise<void>;
    trackedPromise = this.connectAtEpoch(epoch).finally(() => {
      if (this.connectionPromise === trackedPromise) this.connectionPromise = null;
    });
    this.connectionPromise = trackedPromise;
    return trackedPromise;
  }

  private async connectAtEpoch(epoch: number): Promise<void> {
    this.emitConnection({ status: 'connecting', retryCount: 0 });
    let inner: OpenClawProvider | null = null;
    try {
      inner = await this.ensureInitialized();
      if (epoch !== this.connectionEpoch) {
        this.disconnectInner(inner);
        return;
      }
      await inner.connect();
      if (epoch !== this.connectionEpoch) this.disconnectInner(inner);
    } catch (error) {
      if (epoch !== this.connectionEpoch) {
        if (inner) this.disconnectInner(inner);
        return;
      }
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.setState({ phase: 'error', error: normalized.message });
      this.emitConnection({ status: 'error', retryCount: 0, error: normalized });
      if (this.directDesktop) this.scheduleDesktopReconnect();
      throw normalized;
    }
  }

  disconnect(): void {
    this.manuallyDisconnected = true;
    if (this.desktopReconnectTimer) clearTimeout(this.desktopReconnectTimer);
    this.desktopReconnectTimer = undefined;
    this.connectionEpoch += 1;
    this.teardownFallback?.();
    this.teardownFallback = undefined;
    this.unsubscribeInnerConnection?.();
    this.unsubscribeInnerConnection = undefined;
    // CONNECTING 状态直接 close 会让 SDK reject 1006。进行中的 connect 自己在 epoch
    // 失效后完成清理；已稳定/空闲的连接仍立即断开。
    if (!this.connectionPromise) this.inner?.disconnect();
    this.emitConnection({ status: 'disconnected', retryCount: 0 });
  }

  private disconnectInner(inner: OpenClawProvider): void {
    if (this.inner === inner) {
      this.teardownFallback?.();
      this.teardownFallback = undefined;
      this.unsubscribeInnerConnection?.();
      this.unsubscribeInnerConnection = undefined;
    }
    inner.disconnect();
  }

  async request(params: BotChatRequest, messageId?: string): Promise<void> {
    const inner = await this.ensureInitialized();
    // 发送前刷新 IAM token,确保 WS 请求帧携带最新身份凭证(与 supportProvider / open-claw 一致)。
    const freshIamToken = this.directDesktop
      ? ''
      : await this.getIamTokenFn(
          this.options.bot.realBotId,
          resolveUserId(this.options.userId),
          this.options.bot.ownerId,
          this.options.bot.runtimeStage ?? 'online',
        );
    this.iamToken = freshIamToken;
    updateProviderIamToken(inner, freshIamToken);
    if (!inner.isConnected) await this.connect();
    await inner.request(
      {
        query: params.content,
        sessionKey: this.options.sessionId,
        ...(params.resourceReferences ? { resourceReferences: params.resourceReferences } : {}),
        ...(params.promptFileRefs ? { promptFileRefs: params.promptFileRefs } : {}),
      },
      messageId,
    );
  }

  abort(): void {
    this.inner?.abort();
  }
  stop(): void {
    this.abort();
  }

  async loadHistory(): Promise<ChatMessage[]> {
    this.setState({ phase: 'loading-history', error: null });
    try {
      const page = await botSessionService.listMessagesPage(
        this.options.bot,
        this.options.userId,
        this.options.sessionId,
        1,
        BOT_MESSAGE_PAGE_SIZE,
      );
      this.historyPage = 1;
      this.loadedHistoryRawCount = page.rawCount;
      this.historyTotal = page.total;
      const history = page.messages;
      // loadHistory 可能在 connect() 之前预取,此时未连上 → 保持 'idle',
      // 待 SDK 触发 'connected' 事件再切到 'ready';已连上则直接 'ready'。
      this.setState({ phase: this.isConnected ? 'ready' : 'idle', error: null });
      return history;
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.setState({ phase: 'error', error: normalized.message });
      throw normalized;
    }
  }

  async loadMoreHistory(): Promise<ChatMessage[]> {
    if (!this.hasMoreHistory || this.loadingMoreHistory) return [];
    this.loadingMoreHistory = true;
    try {
      const nextPage = this.historyPage + 1;
      const page = await botSessionService.listMessagesPage(
        this.options.bot,
        this.options.userId,
        this.options.sessionId,
        nextPage,
        BOT_MESSAGE_PAGE_SIZE,
      );
      this.historyPage = nextPage;
      this.loadedHistoryRawCount += page.rawCount;
      this.historyTotal = page.total;
      return page.messages;
    } finally {
      this.loadingMoreHistory = false;
    }
  }

  private scheduleDesktopReconnect(): void {
    if (this.manuallyDisconnected || this.desktopReconnectTimer) return;
    const epoch = this.connectionEpoch;
    this.desktopReconnectTimer = setTimeout(() => {
      this.desktopReconnectTimer = undefined;
      if (this.manuallyDisconnected || epoch !== this.connectionEpoch || this.isConnected) return;
      // A desktop restart can change the host port. Rebuild from discovery,
      // instead of asking the SDK to retry its stale WebSocket URL.
      void this.reconnect().catch(() => this.scheduleDesktopReconnect());
    }, 5000);
  }

  async reconnect(): Promise<void> {
    const pendingConnection = this.connectionPromise;
    this.disconnect();
    const epoch = this.connectionEpoch;
    if (pendingConnection) await pendingConnection.catch(() => undefined);
    if (epoch !== this.connectionEpoch) return;
    this.inner = null;
    this.initializePromise = null;
    await this.connect();
  }

  private setState(patch: Partial<BotChatState>): void {
    this.state = { ...this.state, ...patch };
    this.stateListeners.forEach((l) => l(this.state));
  }
  private emitConnection(event: ConnectionStatusEvent): void {
    this.connectionListeners.forEach((l) => l(event));
  }
}

export function createBotChatProvider(options: BotChatProviderOptions): BotChatProvider {
  return new BotChatProvider(options);
}
