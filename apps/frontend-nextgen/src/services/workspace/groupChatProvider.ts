import type { GroupExecResolution } from '@/domain/collaboration/groupExecApprovals';
import { GroupExecApprovalService, type GroupExecApprovalSnapshot } from './groupExecApprovalService';
import type { GroupQuestionResolution } from '@/domain/collaboration/groupQuestions';
import { parseConnectSpeakingMode } from '@/domain/collaboration/speakingLease';
import { GroupSpeakingLeaseService, type SpeakingLeaseSnapshot } from './groupSpeakingLeaseService';
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
import { normalizeHumanUserId } from '@/domain/userIdentity';
import { GroupChatHistoryPaginator } from './groupChatHistoryPaginator';
import {
  buildGroupChatPayload,
  buildGroupWsUrl,
  isDeliveryUpdatedFrame,
  isViewScopeChangedFrame,
  parseDeliveryStatusView,
} from './groupChatProviderHelpers';
import { GroupQuestionService, type GroupQuestionSnapshot } from './groupQuestionService';

/** 透出 WS URL 构造器，保持既有 import 路径稳定（历史分页等纯函数已下沉到独立模块）。 */
export { buildGroupWsUrl } from './groupChatProviderHelpers';

/** 协作群对话请求参数（与 useChat 组合时的 conversationKey = session_id）。 */
export interface GroupChatRequest {
  content: string;
  sessionId: string;
  /** 被 human @ 的会话成员 ID 列表（Bot UUID / human actor ID）；@ALL 在 UI 层展开为全部 Bot。 */
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
  id?: string;
  method?: string;
  params?: Record<string, unknown>;
};

type SessionFrameTransport = {
  send: (frame: unknown) => Promise<unknown>;
};

export interface GroupChatAbortResult {
  aborted: boolean;
  abortedRunIds: string[];
}

interface BotAbortCapableProvider {
  abortBot(input: { groupId: string; sessionId: string; botId: string }): Promise<GroupChatAbortResult>;
}

interface GroupChatProviderOptions {
  sessionId: string;
  /** 群 ID——BCS connect 帧的 group_id（旧「我的协作」协议）。 */
  groupId: string;
  identityId: string;
  /** 登录 Human 的 userId；发言权持有者判定必须与认证连接一致。 */
  authenticatedUserId?: string | null;
  /** HTTP 历史始终可读；只有实时连接及发送受此开关控制。 */
  liveEnabled?: boolean;
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
  private liveAllowed: boolean;
  private transportGeneration = 0;
  private inner: SdkGroupChatProvider | null = null;
  private initializePromise: Promise<SdkGroupChatProvider> | null = null;
  /** reconnect in-flight 守卫：并发重连共享同一 promise（只重拉一次 token）。 */
  private reconnectPromise: Promise<void> | null = null;
  /**
   * ask_user 交互生命周期 service——本 Provider 实例私有（快照/等待/幂等缓存随实例隔离）。
   * 组件只经 subscribeToQuestions/resolveQuestion 消费，不接触 transport 或协议帧。
   */
  private readonly questionService: GroupQuestionService;
  private readonly execApprovalService: GroupExecApprovalService;
  /**
   * 发言权租约生命周期 service——本 Provider 实例私有（快照/等待随实例隔离）。
   * 组件只经 subscribeToSpeakingLease / acquireSpeakingLease 等方法消费。
   */
  private readonly speakingLeaseService: GroupSpeakingLeaseService;
  /**
   * 最近一次出站 connect 帧的 id：connect 应答以 id 识别，读取 speaking_mode
   * 后即失效（undefined = 无在途 connect 应答可认领）。
   */
  private pendingConnectFrameId?: string;
  /**
   * 本连接周期在途的 chat.send 请求 id：服务端 ok:false 应答按 id 匹配后以
   * chatSendError 广播给 UI（SDK 会静默吞掉请求失败——没有这层通知，用户
   * 对"消息被服务端拒绝"毫无感知）。断开时整体清空。
   */
  private pendingChatSendFrameIds = new Set<string>();
  private chatSendErrorListeners = new Set<(error: { code: string; message: string }) => void>();
  /**
   * 拥有问答状态的连接周期（inner 身份校验）：只有当前周期的 inner 能驱动 questionService。
   * 断开、替换或异步初始化被 disconnect 废弃时置 null——旧回调迟到也不更新新连接状态。
   */
  private questionScopeInner: SdkGroupChatProvider | null = null;
  /** 连接生命周期序号：connect()/disconnect() 递增，用于废立在途异步初始化的问答 scope 认领。 */
  private lifecycleSeq = 0;
  private connectionListeners = new Set<(event: ConnectionStatusEvent) => void>();
  private stateListeners = new Set<(state: GroupChatState) => void>();
  private viewScopeListeners = new Set<() => void>();
  private deliveryListeners = new Set<(delivery: import('@/domain/collaboration/types').DeliveryStatusView) => void>();
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
    this.liveAllowed = options.liveEnabled ?? true;
    this.historyPaginator = new GroupChatHistoryPaginator(options.sessionId, options.identityId);
    this.questionService = new GroupQuestionService({
      groupId: options.groupId,
      bcsSessionId: options.sessionId,
      // 动态发送：经当前 inner 的 transport 出站（替换后即刻切到新连接）；真实连接状态 getter，
      // 不使用 UI 展示状态（displayStatus），断线下 resolve 在 service 层立即拒绝。
      send: (frame) => this.sendQuestionFrame(frame),
      isConnected: () => this.isConnected,
    });
    this.execApprovalService = new GroupExecApprovalService({
      groupId: options.groupId,
      bcsSessionId: options.sessionId,
      send: (frame) => this.sendQuestionFrame(frame),
      isConnected: () => this.isConnected,
    });
    const authenticatedHumanId = normalizeHumanUserId(options.authenticatedUserId, 'human');
    const humanUserId = authenticatedHumanId || options.identityId;
    this.speakingLeaseService = new GroupSpeakingLeaseService({
      groupId: options.groupId,
      // 服务端 holderActorId 形如 human_{userId}；认证 Human 优先，不能沿用 Bot 查看身份。
      humanActorId: `human_${humanUserId}`,
      send: (frame) => this.sendQuestionFrame(frame),
      isConnected: () => this.isConnected,
    });
  }

  get isConnected(): boolean {
    return this.liveAllowed && (this.inner?.isConnected ?? false);
  }

  get liveEnabled(): boolean {
    return this.liveAllowed;
  }

  setLiveEnabled(enabled: boolean): void {
    this.liveAllowed = enabled;
    if (!enabled) this.disconnect();
  }

  private assertLiveGeneration(generation: number): void {
    if (!this.liveAllowed) throw new Error('未加入当前会话，无法发送消息。');
    if (generation !== this.transportGeneration) throw new Error('协作连接已取消。');
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

  /** 订阅服务端 view_scope_changed 关闭事件（scope 在线变更，前端需重连）。 */
  subscribeToViewScopeChanged(listener: () => void): () => void {
    this.viewScopeListeners.add(listener);
    return () => {
      this.viewScopeListeners.delete(listener);
    };
  }

  /** 订阅服务端 message.delivery.updated 投递状态变更事件。 */
  subscribeToDeliveryUpdates(
    listener: (delivery: import('@/domain/collaboration/types').DeliveryStatusView) => void,
  ): () => void {
    this.deliveryListeners.add(listener);
    return () => {
      this.deliveryListeners.delete(listener);
    };
  }

  /**
   * 订阅当前群/会话的 ask_user 交互快照（委托 questionService，订阅即回放当前状态）。
   * reason 区分 update/terminal/reset：reset 表示连接周期重置，业务侧暂存草稿等待重放。
   */
  subscribeToExecApprovals(listener: (snapshot: GroupExecApprovalSnapshot) => void): () => void {
    return this.execApprovalService.subscribe(listener);
  }

  resolveExecApproval(key: string, resolution: GroupExecResolution): Promise<void> {
    return this.execApprovalService.resolve(key, resolution);
  }

  subscribeToQuestions(listener: (snapshot: GroupQuestionSnapshot) => void): () => void {
    return this.questionService.subscribe(listener);
  }

  /**
   * 提交问答答案 / 取消整组（委托 questionService）。
   * Promise 在匹配 ACK 或终态竞态时成功；失败拒绝结构化 GroupQuestionResolveError。
   */
  resolveQuestion(key: string, resolution: GroupQuestionResolution): Promise<void> {
    return this.questionService.resolve(key, resolution);
  }

  /** 订阅发言权快照（委托 speakingLeaseService，订阅即回放当前状态）。 */
  subscribeToSpeakingLease(listener: (snapshot: SpeakingLeaseSnapshot) => void): () => void {
    return this.speakingLeaseService.subscribe(listener);
  }

  /**
   * 获取（preempt=false）/ 抢占（preempt=true）发言权。
   * 成功返回 true；被占用返回 false；指令失败拒绝 LeaseCommandError。
   */
  acquireSpeakingLease(preempt = false): Promise<boolean> {
    return this.speakingLeaseService.acquire(preempt);
  }

  /** 释放当前持有的发言权；失败拒绝 LeaseCommandError，此刻不清 UI 状态。 */
  releaseSpeakingLease(): Promise<void> {
    return this.speakingLeaseService.release();
  }

  /** lease.current 补拍快照（含轮询收敛；失败落 error 态）。 */
  refreshSpeakingLease(): Promise<void> {
    return this.speakingLeaseService.refresh();
  }

  /**
   * 订阅本连接 chat.send 的服务端拒绝（"无效操作/发言权缺失"等）：SDK 对请求
   * 失败静默，UI 没有这层通知就感知不到消息根本没被接受。
   */
  subscribeToChatSendErrors(
    listener: (error: { code: string; message: string }) => void,
  ): () => void {
    this.chatSendErrorListeners.add(listener);
    return () => {
      this.chatSendErrorListeners.delete(listener);
    };
  }

  private emitViewScopeChanged(): void {
    this.viewScopeListeners.forEach((listener) => listener());
  }

  private emitDeliveryUpdate(delivery: import('@/domain/collaboration/types').DeliveryStatusView): void {
    this.deliveryListeners.forEach((listener) => listener(delivery));
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
    this.assertLiveGeneration(this.transportGeneration);
    if (this.inner) return Promise.resolve(this.inner);
    if (this.initializePromise) return this.initializePromise;
    const pending = this.initialize().catch((error) => {
      if (this.initializePromise === pending) this.initializePromise = null;
      throw error;
    });
    this.initializePromise = pending;
    return pending;
  }

  private async initialize(): Promise<SdkGroupChatProvider> {
    const generation = this.transportGeneration;
    this.setState({ phase: 'preparing', error: null });
    const token = await this.getFreshToken();
    this.assertLiveGeneration(generation);
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
    this.patchIncomingFrames(inner);

    if (this.historyHydrationActive) {
      (inner as HydrationCapableGroupChatProvider).beginHistoryHydration?.();
    }

    const isCurrent = () => this.liveAllowed && this.inner === inner;
    inner.onMessage = (message) => { if (isCurrent()) this.dispatchOrBuffer({ kind: 'message', message }); };
    inner.onComplete = (messages) => { if (isCurrent()) this.dispatchOrBuffer({ kind: 'complete', messages }); };
    inner.onError = (error) => { if (isCurrent()) this.onError?.(error); };

    this.unsubscribeInnerConnection?.();
    this.inner = inner;
    this.unsubscribeInnerConnection = inner.subscribeToConnectionStatus((event) => {
      if (!isCurrent()) return;
      if (event.status === 'connected') this.setState({ phase: 'ready', error: null });
      if (event.status === 'error') {
        this.setState({ phase: 'error', error: event.error?.message || '连接失败' });
      }
      // 断线（含 SDK 内部重连失败）立即结束问答响应等待并清空快照；connected 通知不重置——
      // connect ACK 后服务端重放的 Pending 必须保留（协议 §7.3），快照重建只发生在下一次真实握手。
      // 发言权同步停轮询/到期定时器并立结在途租约请求，避免断开的连接周期继续驱动定时器。
      if (event.status === 'disconnected' && this.questionScopeInner === inner) {
        this.questionService.resetConnection();
        this.execApprovalService.resetConnection();
        this.speakingLeaseService.resetConnection();
      }
      this.emitConnection(event);
    });

    return inner;
  }

  /**
   * 为旧版 SDK 的 BCS 帧补齐会话标识。
   *
   * 该适配只作用于当前 Provider 的出站帧，不改 API 合同、领域模型或 SDK 包；
   * 新版 SDK 若自行生成 session 字段，也统一覆盖为当前会话，避免重连沿用 main。
   *
   * 同时在每个真实 connect 握手 START（出站 connect 帧，覆盖显式连接与 SDK 自动
   * 重连重发）开启新的问答连接周期：上链路前重置快照与等待，由服务端 ACK 后重放的
   * Pending 重建（协议 §7.3）。
   */
  private patchSessionFrames(inner: SdkGroupChatProvider): void {
    const transport = (inner as unknown as { transport?: SessionFrameTransport }).transport;
    if (!transport || typeof transport.send !== 'function') return;

    const originalSend = transport.send.bind(transport);
    const { sessionId } = this.options;
    transport.send = (frame: unknown) => {
      if (!this.liveAllowed || this.inner !== inner) return Promise.reject(new Error('协作连接已取消。'));
      const isConnectHandshake =
        !!frame && typeof frame === 'object' && (frame as SessionFrame).method === 'connect';
      if (isConnectHandshake && this.questionScopeInner === inner) {
        this.questionService.resetConnection();
        this.execApprovalService.resetConnection();
        // connect 应答以帧 id 识别：在途应答到达时读 speaking_mode（一个时刻至多一个）。
        this.pendingConnectFrameId = (frame as SessionFrame).id;
      }
      if (!frame || typeof frame !== 'object') return originalSend(frame);
      const target = frame as SessionFrame;
      if (!target.params || typeof target.params !== 'object') return originalSend(frame);
      if (target.method !== 'connect' && target.method !== 'chat.send') return originalSend(frame);

      const params: Record<string, unknown> = { ...target.params, session_id: sessionId };
      if (target.method === 'connect') params.view_actor_id = this.options.identityId;
      if (target.method === 'chat.send') {
        params.sessionKey = sessionId;
        if (typeof target.id === 'string') this.pendingChatSendFrameIds.add(target.id);
        // lease_required 会话：服务端要求 chat.send 携带本会有效租约；临期（≤5s）
        // 租约不注入——服务端逐条判定为权威，前端不据此放宽发送。
        const leaseParams = this.speakingLeaseService.usableLeaseParams(Date.now());
        if (leaseParams) {
          params.leaseId = leaseParams.leaseId;
          params.leaseEpoch = leaseParams.leaseEpoch;
        }
      }
      return originalSend({ ...target, params });
    };
  }

  /**
   * 拦截入站帧：问答消费优先——仅当前连接周期的 ask_user 交互事件与等待中请求的响应
   * 被 questionService 吃掉；已替换/废弃的旧 inner 回调直接丢弃，不能恢复状态或发起重连。
   * 其余帧按既有链路处理：view_scope_changed 通知并整体重连、delivery.updated 通知订阅者；
   * 事件帧不透传给 SDK parser。
   */
  private patchIncomingFrames(inner: SdkGroupChatProvider): void {
    const transport = (inner as unknown as { transport?: { onMessage?: (msg: unknown) => void } }).transport;
    if (!transport || typeof transport.onMessage !== 'function') return;
    const originalOnMessage = transport.onMessage.bind(transport);
    transport.onMessage = (msg: unknown) => {
      if (!this.liveAllowed || this.inner !== inner) return;
      // chat.send 请求失败可见化：ok:false 应答按在途 id 匹配后广播错误。
      // 不吃帧——SDK 照常收到应答，这里只做旁路通知。
      if (this.isChatSendErrorResponse(msg)) {
        const frame = msg as { id: string; error?: { code?: string; message?: string } };
        this.pendingChatSendFrameIds.delete(frame.id);
        const error = frame.error ?? {};
        this.chatSendErrorListeners.forEach((listener) =>
          listener({
            code: error.code ?? 'chat_send_failed',
            message: error.message ?? '消息发送失败',
          }),
        );
      }
      if (this.questionScopeInner === inner && this.speakingLeaseService.handleFrame(msg)) {
        return;
      }
      if (this.questionScopeInner === inner && this.isPendingConnectResponse(msg)) {
        // connect 应答读 speaking_mode 后即失效；不吞帧——SDK 依赖 connect 应答，
        // 读毕照常透传 originalOnMessage。
        this.pendingConnectFrameId = undefined;
        this.speakingLeaseService.setSpeakingMode(
          parseConnectSpeakingMode((msg as { payload?: Record<string, unknown> }).payload),
        );
      }
      if (this.questionScopeInner === inner && (this.questionService.handleFrame(msg) || this.execApprovalService.handleFrame(msg))) {
        return;
      }
      if (isViewScopeChangedFrame(msg)) {
        this.emitViewScopeChanged();
        void this.reconnect();
        return;
      }
      if (isDeliveryUpdatedFrame(msg)) {
        const delivery = parseDeliveryStatusView(msg);
        if (delivery) this.emitDeliveryUpdate(delivery);
        return;
      }
      return originalOnMessage(msg);
    };
  }

  async connect(): Promise<void> {
    if (!this.liveAllowed) return;
    const generation = this.transportGeneration;
    const lifecycleSeq = ++this.lifecycleSeq;
    this.emitConnection({ status: 'connecting', retryCount: 0 });
    try {
      const inner = await this.ensureInitialized();
      this.assertLiveGeneration(generation);
      if (this.lifecycleSeq === lifecycleSeq) this.questionScopeInner = inner;
      await inner.connect({ groupId: this.options.groupId, sessionId: this.options.sessionId });
      if (generation !== this.transportGeneration || !this.liveAllowed) {
        inner.disconnect();
        return;
      }
      this.assertConnected(inner);
    } catch (error) {
      if (generation !== this.transportGeneration || !this.liveAllowed) return;
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.setState({ phase: 'error', error: normalized.message });
      this.emitConnection({ status: 'error', retryCount: 0, error: normalized });
      throw normalized;
    }
  }

  disconnect(): void {
    // 废立在途的 connect/初始化 scope 认领；旧连接帧即刻失效；立即结束问答在途等待与定时器。
    this.lifecycleSeq += 1;
    this.transportGeneration += 1;
    this.questionScopeInner = null;
    this.pendingConnectFrameId = undefined;
    this.pendingChatSendFrameIds.clear();
    this.questionService.resetConnection();
    this.execApprovalService.resetConnection();
    this.speakingLeaseService.resetConnection();
    const inner = this.inner;
    this.inner = null;
    this.initializePromise = null;
    this.reconnectPromise = null;
    this.unsubscribeInnerConnection?.();
    this.unsubscribeInnerConnection = undefined;
    this.bufferedEvents = [];
    inner?.disconnect();
    this.emitConnection({ status: 'disconnected', retryCount: 0 });
  }

  private assertConnected(inner: SdkGroupChatProvider): void {
    if (!inner.isConnected) throw new Error('协作会话 WebSocket 未建立。');
  }

  /** 在途 connect 应答识别：res 帧 id 精确匹配最近一次出站 connect 帧。 */
  private isPendingConnectResponse(msg: unknown): msg is { payload?: Record<string, unknown> } {
    if (!this.pendingConnectFrameId || typeof msg !== 'object' || msg === null) return false;
    const candidate = msg as Record<string, unknown>;
    return (
      candidate.type === 'res' && candidate.id === this.pendingConnectFrameId
    );
  }

  /** 在途 chat.send 的失败应答识别：res、ok=false、id 命中本周期在途集合。 */
  private isChatSendErrorResponse(msg: unknown): msg is {
    id: string;
    error?: { code?: string; message?: string };
  } {
    if (typeof msg !== 'object' || msg === null) return false;
    const candidate = msg as Record<string, unknown>;
    if (candidate.type !== 'res' || candidate.ok !== false) return false;
    return (
      typeof candidate.id === 'string' && this.pendingChatSendFrameIds.has(candidate.id)
    );
  }

  async request(params: GroupChatRequest, messageId?: string): Promise<void> {
    const generation = this.transportGeneration;
    const lifecycleSeq = ++this.lifecycleSeq;
    const inner = await this.ensureInitialized();
    this.assertLiveGeneration(generation);
    if (!inner.isConnected) {
      if (this.lifecycleSeq === lifecycleSeq) this.questionScopeInner = inner;
      await inner.connect({ groupId: this.options.groupId, sessionId: this.options.sessionId });
      if (generation !== this.transportGeneration || !this.liveAllowed) inner.disconnect();
      this.assertLiveGeneration(generation);
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
    this.inner?.abort();
  }

  /** 只接收目标 Bot；group/session scope 固定取 Provider 构造参数，组件无法跨会话伪造。 */
  async abortBot(botId: string): Promise<GroupChatAbortResult> {
    const generation = this.transportGeneration;
    const lifecycleSeq = ++this.lifecycleSeq;
    const inner = await this.ensureInitialized();
    this.assertLiveGeneration(generation);
    if (!inner.isConnected) {
      if (this.lifecycleSeq === lifecycleSeq) this.questionScopeInner = inner;
      await inner.connect({ groupId: this.options.groupId, sessionId: this.options.sessionId });
      if (generation !== this.transportGeneration || !this.liveAllowed) inner.disconnect();
      this.assertLiveGeneration(generation);
    }
    return (inner as SdkGroupChatProvider & BotAbortCapableProvider).abortBot({
      groupId: this.options.groupId,
      sessionId: this.options.sessionId,
      botId,
    });
  }

  /**
   * interaction.resolve 出站：动态经当前 inner 的 transport 发送（替换连接后自动指向新链路）。
   * inner 缺失（首次建链前 / 已释放）视为发送失败，由 service 归一为可重试错误。
   */
  private sendQuestionFrame(frame: unknown): Promise<unknown> {
    const transport = (this.inner as unknown as { transport?: SessionFrameTransport } | null)?.transport;
    if (!transport || typeof transport.send !== 'function') {
      return Promise.reject(new Error('协作会话 WebSocket 尚未建立。'));
    }
    return transport.send(frame);
  }

  async loadHistory(): Promise<ChatMessage[]> {
    this.setState({ phase: 'loading-history', error: null });
    try {
      const messages = await this.historyPaginator.loadLatest();
      this.setState({ phase: 'ready', error: null });
      return messages.map((message) => this.hydrateMessage(this.inner, message));
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
      const messages = await this.historyPaginator.loadOlder();
      return messages.map((message) => this.hydrateMessage(this.inner, message));
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      throw normalized;
    }
  }

  private hydrateMessage(inner: SdkGroupChatProvider | null, message: ChatMessage): ChatMessage {
    if (!inner) return message;
    const hydrateRun = (inner as HydrationCapableGroupChatProvider).hydrateRun;
    return typeof hydrateRun === 'function' ? hydrateRun.call(inner, message) : message;
  }

  /**
   * 重置初始化状态并重新连接——供业务层在断线恢复时主动调用。
   * in-flight 守卫：并发调用（视角切换 bump + view_scope_changed 帧 + 手动重连）
   * 共享同一 promise，只重拉一次 token / 只重建一次 SDK Provider。
   */
  async reconnect(): Promise<void> {
    if (!this.liveAllowed) return;
    if (this.reconnectPromise) return this.reconnectPromise;
    const pending = this.reconnectInner().finally(() => {
      if (this.reconnectPromise === pending) this.reconnectPromise = null;
    });
    this.reconnectPromise = pending;
    return pending;
  }

  private async reconnectInner(): Promise<void> {
    this.disconnect();
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
