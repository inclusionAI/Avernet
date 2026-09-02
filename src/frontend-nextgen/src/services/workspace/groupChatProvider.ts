import {
  createSessionToken,
  type SessionMessageAttachment,
} from '@/services/backendApi/collaboration/sessionController';
import {
  GroupChatProvider as SdkGroupChatProvider,
  type ConnectionStatusEvent,
  type GroupChatProviderConfig,
} from '@tc-chat/adapters';
import type { AixContext, ChatMessage, ChatProvider } from '@tc-chat/core';
import { GroupChatHistoryPaginator } from './groupChatHistoryPaginator';
import { buildGroupChatPayload, buildGroupWsUrl } from './groupChatProviderHelpers';

/** 透出 WS URL 构造器，保持既有 import 路径稳定（历史分页等纯函数已下沉到独立模块）。 */
export { buildGroupWsUrl } from './groupChatProviderHelpers';

/** 协作群对话请求参数（与 useChat 组合时的 conversationKey = session_id）。 */
export interface GroupChatRequest {
  content: string;
  sessionId: string;
  /** 被 human @ 的 bot UUID 列表；@ALL 在 UI 层展开为全部 bot。 */
  mentions?: string[];
  /** 指定路由目标 bot UUID（桥路径 @指定 bot 时由 buildRequestParams 透传；缺省由身份兜底）。 */
  botUuid?: string;
  /** 是否 @全体（SDK buildBCSChatRequest 据此展开 targetBotUuid）。 */
  mentionAll?: boolean;
  /** 回复指定消息 ID（挂回复串）。 */
  replyToMessageId?: string;
  /** BCS 图片附件（会话文件上传完成后生成的分享链接形态）。 */
  attachments?: SessionMessageAttachment[];
  /** 副屏上下文（SDK buildBCSChatRequest 在 !isInject 且 shouldSendPanelContext 时注入 ws 帧）。 */
  panelContext?: AixContext;
  /** 是否为注入消息（注入消息走 omitPanelContextForInject 分支，不挂 panelContext）。 */
  isInject?: boolean;
}

/** Provider 内部阶段状态，供 Hook / 组件展示连接准备状态。 */
export interface GroupChatState {
  phase: 'idle' | 'preparing' | 'loading-history' | 'ready' | 'error';
  error: string | null;
}

type HydrationCapableGroupChatProvider = SdkGroupChatProvider & {
  beginHistoryHydration?: () => void;
  enterLiveMode?: () => void;
  hydrateRun?: (message: ChatMessage) => ChatMessage;
};

type SessionFrame = {
  method?: string;
  params?: Record<string, unknown>;
};

type SessionFrameTransport = {
  send: (frame: unknown) => Promise<unknown>;
};

interface GroupChatProviderOptions {
  sessionId: string;
  /** 群 ID——BCS connect 帧的 group_id（旧「我的协作」协议）。 */
  groupId: string;
  identityId: string;
  /** 同源 ws(s) 前缀；测试 / SSR 场景下传入避免访问 window。默认基于 window.location 推导。 */
  wsOrigin?: string;
  /** 注入 SDK GroupChatProvider 构造（测试 stub）；缺省走真实 SDK。 */
  createSdkProvider?: (config: GroupChatProviderConfig) => SdkGroupChatProvider;
}

/**
 * 协作群对话 Provider——封装 SDK GroupChatProvider（BCS 协议）生命周期：
 *  - 一次性拉取 session token（共享 initializePromise，并发 connect 不重发）
 *  - 依据 token 构造同源 ws URL，注入 SDK Provider
 *  - connect/chat.send 帧通过兼容适配器补齐 session_id（当前 SDK 未原生支持 sessionId）
 *  - 转发连接状态、loadHistory 委托 GroupChatHistoryPaginator 走 collaboration sessionController
 *
 * 不含 IAM 轮询——新网关 token 已携带调用者身份。
 */
export class GroupChatProvider implements ChatProvider<GroupChatRequest> {
  public onMessage?: (message: ChatMessage) => void;
  public onComplete?: (messages: ChatMessage[]) => void;
  public onError?: (error: Error) => void;

  // 显式声明支持并发请求(对齐 SDK GroupChatProvider.js:42):useChat.onRequest 在 isRequesting && supportsConcurrentRequests 时走 provider.request 直发,否则 chat.send 静默丢(根因 B')。
  public readonly supportsConcurrentRequests = true;

  private readonly options: GroupChatProviderOptions;
  private inner: SdkGroupChatProvider | null = null;
  private initializePromise: Promise<SdkGroupChatProvider> | null = null;
  private connectionListeners = new Set<(event: ConnectionStatusEvent) => void>();
  private stateListeners = new Set<(state: GroupChatState) => void>();
  private unsubscribeInnerConnection?: () => void;
  private state: GroupChatState = { phase: 'idle', error: null };
  private bufferLiveEvents = true;
  private historyHydrationActive = true;
  private bufferedEvents: Array<
    { kind: 'message'; message: ChatMessage } | { kind: 'complete'; messages: ChatMessage[] }
  > = [];

  /** 协作群历史消息向上翻页分页器（游标 / hasMore / 加载态由其内部管理）。 */
  private readonly historyPaginator: GroupChatHistoryPaginator;

  constructor(options: GroupChatProviderOptions) {
    this.options = options;
    this.historyPaginator = new GroupChatHistoryPaginator(options.sessionId, options.identityId);
  }

  get isConnected(): boolean {
    return this.inner?.isConnected ?? false;
  }

  get supportState(): GroupChatState {
    return this.state;
  }

  /** 是否还有更早的历史消息可加载（供 Hook / UI 控制顶部「加载更多」显隐）。 */
  get hasMoreHistory(): boolean {
    return this.historyPaginator.hasMore;
  }

  /** 是否正在向上翻页加载更早的消息（供 UI 展示顶部加载指示器）。 */
  get isLoadingMoreHistory(): boolean {
    return this.historyPaginator.isLoadingMore;
  }

  /** 订阅 Provider 阶段状态（preparing / loading-history / ready / error）。 */
  subscribeToSupportState(listener: (state: GroupChatState) => void): () => void {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => {
      this.stateListeners.delete(listener);
    };
  }

  /** 订阅底层 WebSocket 连接状态变化。 */
  subscribeToConnectionStatus(listener: (event: ConnectionStatusEvent) => void): () => void {
    this.connectionListeners.add(listener);
    return () => {
      this.connectionListeners.delete(listener);
    };
  }

  /**
   * 拉取一次性 session token；失败直接抛出——不降级为 owner 匿名请求（详见 brief 行为要求 2）。
   */
  private async getFreshToken(): Promise<string> {
    const resp = await createSessionToken(this.options.sessionId);
    const token = resp?.data?.token;
    if (!token) throw new Error('协作连接令牌获取失败。');
    return token;
  }

  /**
   * 共享初始化——并发 connect/request 都只会触发一次 token 获取 + 一次 SDK Provider 构造。
   * 失败时清空 initializePromise 允许后续重试。
   */
  private ensureInitialized(): Promise<SdkGroupChatProvider> {
    if (this.inner) return Promise.resolve(this.inner);
    if (this.initializePromise) return this.initializePromise;
    this.initializePromise = this.initialize().catch((error) => {
      this.initializePromise = null;
      throw error;
    });
    return this.initializePromise;
  }

  private async initialize(): Promise<SdkGroupChatProvider> {
    this.setState({ phase: 'preparing', error: null });
    const token = await this.getFreshToken();
    const url = buildGroupWsUrl({ token, wsOrigin: this.options.wsOrigin });
    const factory = this.options.createSdkProvider ?? ((cfg) => new SdkGroupChatProvider(cfg));
    const inner = factory({
      url,
      currentUserId: this.options.identityId,
      groupId: this.options.groupId,
      sessionId: this.options.sessionId,
      reconnectAttempts: 3,
      heartbeatInterval: 30_000,
      heartbeatTimeout: 5 * 60_000,
      connectionTimeout: 10_000,
      enableThinkingTag: true,
      fallbackMessage: '请求失败，请稍后重试',
    });

    // 当前已安装 SDK 的 GroupChatProvider 类型和运行时只处理 group_id，
    // 传入的 sessionId 会被忽略；在包装层保留会话级帧适配，避免重连后落到默认 main 会话。
    this.patchSessionFrames(inner);

    if (this.historyHydrationActive) {
      (inner as HydrationCapableGroupChatProvider).beginHistoryHydration?.();
    }

    inner.onMessage = (message) => this.dispatchOrBuffer({ kind: 'message', message });
    inner.onComplete = (messages) => this.dispatchOrBuffer({ kind: 'complete', messages });
    inner.onError = (error) => this.onError?.(error);

    this.unsubscribeInnerConnection?.();
    this.unsubscribeInnerConnection = inner.subscribeToConnectionStatus((event) => {
      if (event.status === 'connected') this.setState({ phase: 'ready', error: null });
      if (event.status === 'error') {
        this.setState({ phase: 'error', error: event.error?.message || '连接失败' });
      }
      this.emitConnection(event);
    });

    this.inner = inner;
    return inner;
  }

  /**
   * 为旧版 SDK 的 BCS 帧补齐会话标识。
   *
   * 该适配只作用于当前 Provider 的出站帧，不改 API 合同、领域模型或 SDK 包；
   * 新版 SDK 若自行生成 session 字段，也统一覆盖为当前会话，避免重连沿用 main。
   */
  private patchSessionFrames(inner: SdkGroupChatProvider): void {
    const transport = (inner as unknown as { transport?: SessionFrameTransport }).transport;
    if (!transport || typeof transport.send !== 'function') return;

    const originalSend = transport.send.bind(transport);
    const { sessionId } = this.options;
    transport.send = (frame: unknown) => {
      if (!frame || typeof frame !== 'object') return originalSend(frame);
      const target = frame as SessionFrame;
      if (!target.params || typeof target.params !== 'object') return originalSend(frame);
      if (target.method !== 'connect' && target.method !== 'chat.send') return originalSend(frame);

      const params: Record<string, unknown> = { ...target.params, session_id: sessionId };
      if (target.method === 'chat.send') params.sessionKey = sessionId;
      return originalSend({ ...target, params });
    };
  }

  async connect(): Promise<void> {
    this.emitConnection({ status: 'connecting', retryCount: 0 });
    try {
      const inner = await this.ensureInitialized();
      await inner.connect({ groupId: this.options.groupId, sessionId: this.options.sessionId });
      this.assertConnected(inner);
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.setState({ phase: 'error', error: normalized.message });
      this.emitConnection({ status: 'error', retryCount: 0, error: normalized });
      throw normalized;
    }
  }

  disconnect(): void {
    this.inner?.disconnect();
    this.emitConnection({ status: 'disconnected', retryCount: 0 });
  }

  private assertConnected(inner: SdkGroupChatProvider): void {
    if (!inner.isConnected) throw new Error('协作会话 WebSocket 未建立。');
  }

  async request(params: GroupChatRequest, messageId?: string): Promise<void> {
    const inner = await this.ensureInitialized();
    if (!inner.isConnected) {
      await inner.connect({ groupId: this.options.groupId, sessionId: this.options.sessionId });
      this.assertConnected(inner);
    }
    // payload 字段保真装配抽至 buildGroupChatPayload（groupChatProviderHelpers,根因 C);messageId 缺省时不透传第二参。
    const payload = buildGroupChatPayload(params, this.options.groupId, this.options.identityId);
    if (messageId !== undefined) {
      await inner.request(payload, messageId);
    } else {
      await inner.request(payload);
    }
  }

  abort(): void {
    this.inner?.abort(this.options.groupId);
  }

  /** Hook 暴露的别名——与 demo 形态一致。 */
  stop(): void {
    this.abort();
  }

  async loadHistory(): Promise<ChatMessage[]> {
    this.setState({ phase: 'loading-history', error: null });
    try {
      const inner = await this.ensureInitialized();
      const messages = await this.historyPaginator.loadLatest();
      this.setState({ phase: this.isConnected ? 'ready' : 'idle', error: null });
      return messages.map((message) => this.hydrateMessage(inner, message));
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.setState({ phase: 'error', error: normalized.message });
      throw normalized;
    }
  }

  /**
   * 向上翻页加载更早的历史消息——委托分页器以当前最旧时间戳为 `before` 游标请求上一页，
   * 返回更早的 ChatMessage[]（旧→新升序），由 Hook 前置拼接到 SDK chat.messages。
   * 加载失败不切换 phase（保留已加载内容），仅向上抛出供 Hook toast，hasMore 保持原值允许重试。
   */
  async loadMoreHistory(): Promise<ChatMessage[]> {
    try {
      const inner = await this.ensureInitialized();
      const messages = await this.historyPaginator.loadOlder();
      return messages.map((message) => this.hydrateMessage(inner, message));
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      throw normalized;
    }
  }

  private hydrateMessage(inner: SdkGroupChatProvider, message: ChatMessage): ChatMessage {
    const hydrateRun = (inner as HydrationCapableGroupChatProvider).hydrateRun;
    return typeof hydrateRun === 'function' ? hydrateRun.call(inner, message) : message;
  }

  /** 重置初始化状态并重新连接——供业务层在断线恢复时主动调用。 */
  async reconnect(): Promise<void> {
    this.unsubscribeInnerConnection?.();
    this.unsubscribeInnerConnection = undefined;
    this.inner?.disconnect();
    this.inner = null;
    this.initializePromise = null;
    await this.connect();
  }

  /** Start a refresh-safe history hydration window and buffer subsequent WS updates. */
  beginHistoryHydration(): void {
    this.historyHydrationActive = true;
    this.bufferLiveEvents = true;
    this.bufferedEvents = [];
    (this.inner as HydrationCapableGroupChatProvider | null)?.beginHistoryHydration?.();
  }

  /** Deliver WS updates that arrived after connect and after history has been installed. */
  enterLiveMode(): void {
    this.historyHydrationActive = false;
    this.bufferLiveEvents = false;
    (this.inner as HydrationCapableGroupChatProvider | null)?.enterLiveMode?.();
    const events = this.bufferedEvents;
    this.bufferedEvents = [];
    for (const event of events) {
      if (event.kind === 'message') this.onMessage?.(event.message);
      else this.onComplete?.(event.messages);
    }
  }

  private dispatchOrBuffer(
    event: { kind: 'message'; message: ChatMessage } | { kind: 'complete'; messages: ChatMessage[] },
  ): void {
    if (this.bufferLiveEvents) {
      this.bufferedEvents.push(event);
      return;
    }
    if (event.kind === 'message') this.onMessage?.(event.message);
    else this.onComplete?.(event.messages);
  }

  private setState(patch: Partial<GroupChatState>): void {
    this.state = { ...this.state, ...patch };
    this.stateListeners.forEach((listener) => listener(this.state));
  }

  private emitConnection(event: ConnectionStatusEvent): void {
    this.connectionListeners.forEach((listener) => listener(event));
  }
}

/** 工厂——供 useChat / Hook 注入 sessionId/identityId 时构造 Provider。 */
export function createGroupChatProvider(options: GroupChatProviderOptions): GroupChatProvider {
  return new GroupChatProvider(options);
}
