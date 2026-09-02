import { getBotConnection } from '@/services/backendApi/bots/privateBotSessionController';
import { getBotIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import { OpenClawProvider, type ConnectionStatusEvent, type OpenClawProviderConfig } from '@tc-chat/adapters';
import type { ChatMessage, ChatProvider, PromptFileRef, ResourceReference } from '@tc-chat/core';
import { installCompleteFallback } from './botChatCompleteFallback';
import { botSessionService, resolveUserId, type ChatBotView } from './botSessionService';

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
  private readonly getIamTokenFn: typeof getBotIamToken;
  private iamToken = '';
  private connectionListeners = new Set<(event: ConnectionStatusEvent) => void>();
  private stateListeners = new Set<(state: BotChatState) => void>();
  private unsubscribeInnerConnection?: () => void;
  private state: BotChatState = { phase: 'idle', error: null };
  private teardownFallback?: () => void;

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
    const resp = await getBotConnection(this.options.bot.realBotId, {
      user_id: resolveUserId(this.options.userId),
      owner_id: this.options.bot.ownerId,
    });
    const socket = resp.data?.sockets?.find((s) => s.kind === 'chat');
    if (!socket?.url) throw new Error('Bot 连接信息为空,请稍后重试');
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
    this.iamToken = await this.getIamTokenFn(
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
      reconnectAttempts: 3,
      heartbeatInterval: 30_000,
      heartbeatTimeout: 5 * 60_000,
      connectionTimeout: 10_000,
      enableThinkingTag: true,
      fallbackMessage: '请求失败,请稍后重试',
      // SDK 重连前刷新 IAM token(连接 URL 的 token 在 expires_at 内仍有效,SDK 复用原 url;
      // 需换签走 reconnect() 重走 initialize() 重新 getBotConnection)。仅返回 xIAMToken,
      // 不返回 xProxypassToken——避免 SDK 给内含 token 的 url 再次追加 x-proxypass-token。
      credentialProvider: async () => {
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
      this.emitConnection(event);
    });
    this.inner = inner;
    return inner;
  }

  async connect(): Promise<void> {
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

  disconnect(): void {
    if (!this.inner) return;
    this.teardownFallback?.();
    this.inner.disconnect();
    this.emitConnection({ status: 'disconnected', retryCount: 0 });
  }

  async request(params: BotChatRequest, messageId?: string): Promise<void> {
    const inner = await this.ensureInitialized();
    // 发送前刷新 IAM token,确保 WS 请求帧携带最新身份凭证(与 supportProvider / open-claw 一致)。
    const freshIamToken = await this.getIamTokenFn(
      this.options.bot.realBotId,
      resolveUserId(this.options.userId),
      this.options.bot.ownerId,
      this.options.bot.runtimeStage ?? 'online',
    );
    this.iamToken = freshIamToken;
    updateProviderIamToken(inner, freshIamToken);
    if (!inner.isConnected) await inner.connect();
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
      const history = await botSessionService.listMessages(
        this.options.bot,
        this.options.userId,
        this.options.sessionId,
      );
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

  async reconnect(): Promise<void> {
    this.unsubscribeInnerConnection?.();
    this.unsubscribeInnerConnection = undefined;
    this.inner?.disconnect();
    this.inner = null;
    this.initializePromise = null;
    this.teardownFallback?.();
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
