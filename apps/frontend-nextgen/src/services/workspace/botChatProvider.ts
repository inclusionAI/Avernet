import {
  isSendBannedForSession,
  takeCachedA2AProvider,
  type A2aBootstrapConfig,
} from '@/adapters/a2a/bootstrap';
import type { CancelOutcome } from '@/adapters/a2a/cancelOutcomes';
import { getBotConnection } from '@/services/backendApi/bots/privateBotSessionController';
import { getBotIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import { OpenClawProvider, type ConnectionStatusEvent, type OpenClawProviderConfig } from '@tc-chat/adapters';
import type { ChatMessage, ChatProvider, PromptFileRef, ResourceReference } from '@tc-chat/core';
import { installCompleteFallback } from './botChatCompleteFallback';
import {
  BOT_MESSAGE_PAGE_SIZE,
  botSessionService,
  resolveChatRuntimeStage,
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
  /**
   * 回滚换轨后**保留的未终结 A2A 旧任务处理器**（review R2-3 第二补丁，
   * spec §3.1：旧任务生命周期独立于新发送 provider——恢复锚/播报/迟到
   * 核销全在；至终态或用户明确放弃时释放，见 releaseSettledA2aHandler）
   */
  private retiredA2aHandler: OpenClawProvider | null = null;
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
  /** A2A M2 bootstrap seam：最近一次连接信息里的 ws url——§3.1 发送禁令门按它派生 A2A endpointBase */
  private wsUrl: string | null = null;
  /** B17 服务端签发面：连接响应 sockets kind='a2a' 的 base（无则 null——旧后端
   * 不出该 socket，ws 推导行为逐字节保持）；A2A bootstrap 与发送禁令的门键均
   * 以它为权威来源（见 bootstrap.ts resolveA2aUrls/isSendBannedForSession）。 */
  private a2aBaseUrl: string | null = null;
  /** B12 取消四态的宿主订阅面（A2AProvider.onCancelOutcome → useBotChat toast 消费） */
  private readonly cancelOutcomeListeners = new Set<(outcome: CancelOutcome) => void>();
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

  // ─── A2A M2 bootstrap seam：§3.1 发送禁令门（纯谓词）+ B12 取消四态订阅面 ───

  /**
   * §3.1 context 级发送禁令门（spec 原文文案单源在 bootstrap.SEND_BAN_UI_TEXT，
   * toast 消费面归 useBotChat.send）。禁令生命周期独立于 provider 实例
   * （registry 是 (endpointBase, sessionKey) 键的 app 级单例）；未初始化
   * （尚无 ws url）/ 非 A2A 面（选择探测落 v3）恒 false——v3 发送不受影响。
   */
  isSendBanned(): boolean {
    // B17：签发 base 在场时按它解析禁令键（与 provider 注册面同一 endpoint
    // 口径），否则回退既有的 ws 推导——两态都是 bootstrap 单源函数。
    return this.wsUrl !== null
      && isSendBannedForSession(this.options.sessionId, this.wsUrl, this.a2aBaseUrl ?? undefined);
  }

  /** B12 取消终局播报订阅：A2AProvider.onCancelOutcome 经本类转发（含 provider 已 teardown 的迟到核销） */
  subscribeToCancelOutcome(listener: (outcome: CancelOutcome) => void): () => void {
    this.cancelOutcomeListeners.add(listener);
    return () => {
      this.cancelOutcomeListeners.delete(listener);
    };
  }

  private emitCancelOutcome(outcome: CancelOutcome): void {
    for (const listener of [...this.cancelOutcomeListeners]) {
      try {
        listener(outcome);
      } catch {
        /* 单订阅异常不中断其余（镜像订阅面通用容错） */
      }
    }
  }

  private async getChatUrl(): Promise<{ url: string; a2aBaseUrl: string | null }> {
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
    // B17：服务端签发的 A2A base 原样收下（签发方承诺浏览器可达）；
    // 缺席 = null = 既有 ws 推导兜底，不做任何本地代填。
    const a2aBase = resp.data?.sockets?.find((s) => s.kind === 'a2a')?.url;
    return { url: socket.url, a2aBaseUrl: a2aBase ? String(a2aBase) : null };
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
    const { url, a2aBaseUrl } = await this.getChatUrl();
    this.wsUrl = url;
    this.a2aBaseUrl = a2aBaseUrl;
    // 连接凭证(URL query token,管 WS 握手)与请求身份凭证(x-iam-token,管 chat.send 帧)是两套:
    // URL 已内含握手凭证;此处须单独拉取 IAM token 并注入 SDK,否则发送会被服务端拒(身份不明)。
    this.iamToken = this.directDesktop
      ? ''
      : await this.getIamTokenFn(
          this.options.bot.realBotId,
          resolveUserId(this.options.userId),
          this.options.bot.ownerId,
          resolveChatRuntimeStage(this.options.bot),
        );
    // A2A M2 bootstrap seam：SDK 工厂两条候选——缓存命中同步换入 A2AProvider（两段式
    // 传输选择：探测未决时 takeCachedA2AProvider 内部 fire-and-forget 后台预热、
    // 本轮落回 v3；选择/锁定/禁令编排权威在 @/adapters/a2a/bootstrap.ts 单一编排点）。
    // v3 构造行 `new OpenClawProvider(cfg)` 保持逐字不变（零改动红线，钉板
    // test/services/workspace/botChatProvider.a2a.seam.test.ts）。
    const factory = this.options.createSdkProvider ?? ((cfg) => {
      const a2aProvider = takeCachedA2AProvider(cfg as A2aBootstrapConfig);
      if (a2aProvider) {
        // B12 取消四态播报转发（宿主 toast 消费面在 useBotChat；文案单源 CANCEL_UI_TEXT）。
        // IAM 凭证刷新沿用本类既有通道：request() 的 updateProviderIamToken 直写
        // config.xIAMToken，A2AProvider.resolveIamToken 每次请求重读——无需额外接线。
        a2aProvider.onCancelOutcome = (outcome) => this.emitCancelOutcome(outcome);
        return a2aProvider as unknown as OpenClawProvider;
      }
      return new OpenClawProvider(cfg);
    });
    const inner = factory({
      url,
      // B17：服务端签发的 A2A base 随 cfg 透传——A2A bootstrap 在场即权威，
      // v3 OpenClawProvider 对多出的成员幂等忽略（ws 面不改一字）。
      ...(this.a2aBaseUrl ? { a2aBaseUrl: this.a2aBaseUrl } : {}),
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
          resolveChatRuntimeStage(this.options.bot),
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
    // 会话关闭 = 用户明确放弃（spec §3.1 销毁条件）：保留中的未终结旧任务
    // 处理器随会话一并释放
    if (this.retiredA2aHandler) {
      this.retiredA2aHandler.disconnect();
      this.retiredA2aHandler = null;
    }
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
    await this.rollbackTransportForNewRun();          // 发送边界重估（review R2-3）
    const inner = await this.ensureInitialized();
    // 发送前刷新 IAM token,确保 WS 请求帧携带最新身份凭证(与 supportProvider / open-claw 一致)。
    const freshIamToken = this.directDesktop
      ? ''
      : await this.getIamTokenFn(
          this.options.bot.realBotId,
          resolveUserId(this.options.userId),
          this.options.bot.ownerId,
          resolveChatRuntimeStage(this.options.bot),
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

  /**
   * A2A 传输回滚（review R2-3，spec §7 回滚语义）：开关关 / locked-v3 只在
   * 构造期生效是不够的——会话已有 A2A inner 时，后续发送会一直复用它，新
   * 任务继续以 A2A 创建。发送边界重估"当下选择"：
   *   · inner 非 A2A（v3/未建）→ 直接放行；
   *   · inner 是 A2A 且当下 takeCachedA2AProvider 仍取得到（选择成立）→ 放行；
   *   · inner 是 A2A 但当下取不到（开关关/锁定/未决）→ **只替换"新发送
   *     provider"位**（this.inner），重走 initialize 工厂按当下选择重建（此处
   *     即落 v3）；被替换的旧实例按 spec §3.1 销毁时点分流处置——见
   *     parkOrDisposeRetiredA2a。
   * 不自动重发原任务；在途旧 run 由 isRequesting 互斥保证不与换轨并发
   *（能走到发送边界的会话内 发送位必然空闲——但引擎侧任务未必终结，故旧
   * 处理器不得随换轨销毁，review R2-3 第二补丁）。
   */
  private async rollbackTransportForNewRun(): Promise<void> {
    this.releaseSettledA2aHandler();                 // 先核销已终结的旧任务处理器
    const current = this.inner as { isA2aProvider?: boolean } | null;
    if (!current?.isA2aProvider || !this.wsUrl) return;
    const probeCfg = {
      url: this.wsUrl,
      ...(this.a2aBaseUrl ? { a2aBaseUrl: this.a2aBaseUrl } : {}),
      sessionKey: this.options.sessionId,
    } as A2aBootstrapConfig;
    if (takeCachedA2AProvider(probeCfg)) return;    // 当下选择仍 A2A——复用
    const retired = this.inner as OpenClawProvider;
    this.parkOrDisposeRetiredA2a(retired);
    this.inner = null;
    this.initializePromise = null;
    await this.initialize();                        // 工厂重走：当下选择 → v3
  }

  /**
   * 回滚退役实例的 spec §3.1 分流：**未终结任务**（hasUnterminatedRecovery：
   * 恢复锚在、终局未落、无在途请求——预算耗尽/协议错等退出后的典型形态）→
   * 旧任务处理器**保留**为 retiredA2aHandler——订阅/播报/恢复锚全数不动
   * （manualRecover 仍可开只读轮、迟到 CancelTask 核销仍写 context 级
   * registry、终稿仍走 parser→onComplete 进会话 UI）。**已终结/无任务** →
   * 原 R2-3 语义：disconnectInner 仅断连静默退役（不播 onComplete）。
   * 前端发送位空闲 ≠ 引擎任务已终结——不得以前者证明可销毁后者。
   */
  private parkOrDisposeRetiredA2a(retired: OpenClawProvider): void {
    if ((retired as { hasUnterminatedRecovery?: boolean }).hasUnterminatedRecovery) {
      this.retiredA2aHandler = retired;             // 只让出发送位，处理器全保留
      return;
    }
    this.disconnectInner(retired);
  }

  /**
   * 旧任务处理器的 §3.1 销毁条件核销（在新发送边界惰性执行）：终局已落
   * （runSettled——迟到终态/GetTask 收账后 hasUnterminatedRecovery 翻 false）
   * → 断连释放。GetTask 查无任务（not_found_or_expired）走同一位翻false。
   * "用户明确放弃恢复"（§3.1 第三条件）的 UI 入口未建设——会话级 disconnect
   * 即整体释放（见 disconnect()），独立放弃按钮留 M3。
   */
  private releaseSettledA2aHandler(): void {
    const parked = this.retiredA2aHandler;
    if (!parked) return;
    if ((parked as { hasUnterminatedRecovery?: boolean }).hasUnterminatedRecovery) return;
    this.retiredA2aHandler = null;
    this.disconnectInner(parked);                   // 已终结：只剩断连释悬挂
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
