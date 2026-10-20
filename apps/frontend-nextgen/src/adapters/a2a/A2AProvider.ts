/**
 * B10 — A2AProvider：A2A SSE 流 → 真 OpenClawParser 的核心消费循环。
 * ============================================================================
 *
 * 定位：OpenClawProvider（v3 ws）的 drop-in 替身——同一 ChatProvider 可观察面
 * （onMessage/onComplete/onError/abort/审批/ParseResult 订阅），传输面换 A2A
 * （card 探测门 + JSON-RPC/SSE 单向流），帧形状由 B9 translate 层折算。
 *
 * ─── 钉死的 observable 语义（对拍 v3 OpenClawProvider.js）──────────────────
 *   · abort()：**OpenClawProvider.js:291-299** —— `_isRequesting=false` +
 *     `onComplete([])`，无 onError（ws 版同时发 abort 帧，A2A 的 CancelTask
 *     面属 B11/B12；B10 只断流，onComplete([]) 的完成播报语义不变）。
 *   · run 取消终态（CANCELED/REJECTED）：parser 面对废除帧是纯 no-op（B9 表 /
 *     OpenClawParser.js:637 早退）——A2AProvider 必须区分"完成结果"与"传输
 *     终止"：取消终态由本层直接补发 onComplete([])（与 v3 abort 同形）。
 *   · run 失败终态：state:'error' 帧进 parser.onError（OpenClawParser.js:180-185，
 *     与 event 名无关）→ 本层 parser.onError 钩子按
 *     **OpenClawProvider.js:154-172** 合错误消息（onMessage(errorMsg)+onError）；
 *     request() 拒绝时携带 `.failure`（kind:'rpc'，见 runFailure 注）。
 *   · 传输/协议/鉴权 wire 失败（streamRpc typed throw）：直接 this.onError + 重抛
 *     ——对位 v3 transport.onError（OpenClawProvider.js:98-107）；连接面
 *     （card 探测门）不因单次请求断流掉线。B11 起 run 级恢复接管：known-taskId
 *     断流走 RecoveryRunner（游标重放 + 预算 + GetTask 分诊），未知 taskId 断流
 *     按结果未知止损（prior_to_task_event typed 拒绝）。
 *   · 连接面：connect() 探测成功 notify {connected}（先 {connecting}，对位
 *     OpenClawProvider.js:188-191）；disconnect() notify {disconnected}
 *     （:108-117：断开不发 onComplete）。
 *   · request 互斥 + 未连接自动 connect：对位 :222-236。
 *   · parser.reset()+setSessionKey(同一 sessionKey)：对位 :245-246，
 *     B9 钉板 #2（等值必过 includes 门）。
 *
 * ─── generation 迟帧栅栏 ─────────────────────────────────────────────────
 * abort()/disconnect()/新 request() 都 ++generation；在途 for-await 每圈
 * 迭代先对 generation——被顶替的旧迭代器若还有逃逸帧，一律 break，永不喂 parser
 * （"no parser poison"：abort 后的下一个 request 必须全量可工作）。
 *
 * B12 已落地面：
 *   · abort() 四态机整替 throwaway：known-taskId 在途 → wire CancelTask 控制
 *     面（fire-and-forget，10s 门）+ 乐观挂 context 级 send-ban（§3.1）；
 *     落地结果按 classifyCancel 四态核销禁令（only confirmed releases）+
 *     onCancelOutcome 上行 UI；pre-task abort 零 wire 面（服务端无 run）。
 *   · resolveApproval 真接入：ResolveInteraction 单信封 + 两步 parser 释放；
 *     resolve_unsupported → typed 拒绝、pending 保持（§5）。
 */
import { buildRpcRequest, type WireFailure } from './rpc';
import { rpcOnce } from './rpcOnce';
import { streamRpc, type WireEvent } from './stream';
import { fetchCard, validateScenarios, Scenario } from './card';
import { translateEvent, type TranslateCtx } from './translate';
import {
  RecoveryRunner,
  type RecoveryConfig,
  type RecoveryEngine,
  type RecoveryOutcome,
} from './recovery';
import { classifyCancel, type CancelOutcome } from './cancelOutcomes';
import { createSendBanRegistry, type SendBanRegistry } from './sendBan';
import type { A2aUrls } from './urls';
import {
  loadOpenClawParser,
  type SdkParseResult,
  type SdkParser,
  type SdkPendingApproval,
} from './sdkParser';
import type {
  A2aChatMessage,
  A2aOpenClawInput,
  A2aOpenClawProviderConfig,
} from './A2AProviderInput';

// ─── B12 审批面常量（spec §5；导出供 UI 复用） ────────────────────────────

/** §5 引擎"声明了但实现不支持"（reason=resolve_unsupported，如 OpenClaw 形态）：
 * 决策未被引擎接受——pending 保持、任务不假完成、不静默挂死。 */
export const A2A_APPROVAL_UNSUPPORTED_MESSAGE =
  '当前引擎不支持审批决议，任务保持待审批状态';
/** ask_user 的 allow 决议需要结构化答案载荷（answer/answers/values/
 * selected_options）——v3 decision 枚举无载荷面，B13+ UI 供答案后放开。 */
export const A2A_APPROVAL_ANSWERS_REQUIRED_MESSAGE =
  'a2a approval answers require input';

/** B12：CancelTask 控制面单信封的兜底时限（对位引擎侧 chat.abort 10s RPC 窗；
 * 超时=取消结局不可知 → cancel-unknown 核销，禁令保持）。 */
const CANCEL_RPC_TIMEOUT_MS = 10_000;

/**
 * 模块级缺省 send-ban 注册表：禁令是 context 级状态（键=(rpcUrl, sessionKey)），
 * 与 provider 实例生命周期无关——换代/重建 provider 后同 context 仍见禁令
 * （spec §3.1）。B13 可经 config.sendBan 注入自持实例（测试隔离同路）。
 */
const DEFAULT_SEND_BAN_REGISTRY = createSendBanRegistry();

// ─── B11 恢复面 UI 常量（spec §4.4/§3 信号语，导出供 UI 复用） ────────────

/** replay 拒绝 + GetTask 终态：结果可取回但标"不完整" */
export const RECOVERY_INCOMPLETE_MESSAGE = '恢复不完整，任务结果部分缺失';
/** 未知 taskId 断流（首 Task 事件前）/ not_found_or_expired：结果未知；
 * spec §3 #2 —— 永不自动恢复、永不自动重发 */
export const OUTCOME_UNKNOWN_MESSAGE = '结果未知';
/** 预算耗尽：停止自动请求的信号语（spec §4.4 原文） */
export const RECOVERY_STOPPED_MESSAGE = '恢复已停止，任务结果未确认';

/**
 * run 级业务失败的归类：B9 事实源——wire FAILED 终态只在 statusUpdate metadata
 * 携 errorCode/errorMessage，没有 JSON-RPC 错误信封。归 rpc 桶（rpc.ts 头注
 * "业务 rpc 错误上浮 UI"），以 JSON-RPC 标准 Internal error 码承载。
 */
interface A2aRunFailure extends WireFailure {
  reason: 'run_failed';
  errorCode?: string;
}

/** JSON-RPC Internal error（RFC 早期草案/事实标准码）：run 业务失败的折算承载 */
const RUN_FAILED_RPC_CODE = -32603;

/** v3 同款缺省会话键（OpenClawProvider.js:243） */
const DEFAULT_SESSION_KEY = 'agent:main:main';

/** engine wire.py 终态口径（与 translate.ts 常量同源） */
const STATE_CANCELED = 'TASK_STATE_CANCELED';
const STATE_REJECTED = 'TASK_STATE_REJECTED';
const STATE_COMPLETED = 'TASK_STATE_COMPLETED';
const STATE_FAILED = 'TASK_STATE_FAILED';

/** OpenClawProvider.d.ts:18-27 ConnectionStatusEvent 镜像 */
export interface A2aConnectionStatusEvent {
  status: 'connecting' | 'connected' | 'disconnected' | 'reconnecting' | 'error';
  retryCount: number;
  error?: Error;
  closeCode?: number;
  closeReason?: string;
  wasClean?: boolean;
}

/**
 * 恢复终局信号（B11）的 typed 承载：与 run_failed 同走 rpc 桶上浮 UI；
 * reason 三值与 RecoveryOutcome 的三个"止损面"一一对应。
 * B14/F4：非可重试硬失败（auth/protocol）早退按 recovery_stopped 收口，
 * 原始 failure 经 `cause` 随行（分诊/锁定二判用，不再二次丢失）。
 */
interface A2aRecoverySignalFailure extends WireFailure {
  reason: 'recovery_incomplete' | 'recovery_outcome_unknown' | 'recovery_stopped';
  /** F4：fatal 早退（RecoveryOutcome 'fatal'）时附录的原始 wire 失败 */
  cause?: WireFailure;
}

/** B12 审批面 typed 失败标注（protocol 桶=客户端造词的 typed 拒绝，与
 * ask_user answers-required 同构；rpcError 附引擎原文供分诊） */
interface A2aApprovalFailure extends WireFailure {
  reason: 'approval_unsupported' | 'approval_answers_required';
}

export interface A2AProviderConfig extends A2aOpenClawProviderConfig {
  /** A2A 面地址（由 ws url 派生，见 urls.ts） */
  a2aUrls: A2aUrls;
  /** 动态 IAM 凭证（每次 request 前取新，覆盖静态 xIAMToken） */
  iamTokenGetter?: () => Promise<string | undefined>;
  /** fetch 注入口（测试 seam；缺省全局 fetch） */
  fetchImpl?: typeof fetch;
  /** 恢复预算覆盖（B11：adapter 级测试 seam；缺省 spec §4.4 首版默认值） */
  recovery?: RecoveryConfig;
  /** B12：context 级 send-ban 注册表（§3.1）。缺省模块级共享实例——禁令
   * 生存期独立于 provider 实例；B13 注入自持实例以复用同一 registry。 */
  sendBan?: SendBanRegistry;
}

const EMPTY_PARSE_RESULT: SdkParseResult = {
  blocks: [], content: '', done: false, toolCalls: [], isApproval: false,
};

/** uuidv4 等价：真浏览器 secure context / node jest 均有 crypto.randomUUID；
 * uuid 包在精简 nodeenv 没装，v3 的 `import { v4 } from 'uuid'` 路径
 * （OpenClawProvider.js:8）走不通。 */
function newMessageId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch { /* 下探 */ }
  return `a2a-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
}

/** 终态成员的 status.state 读取（wire 事实：终态只随 statusUpdate 成员下发出） */
function terminalStateOf(member: Record<string, unknown>): string {
  const status = member.status;
  if (typeof status === 'object' && status !== null
    && typeof (status as { state?: unknown }).state === 'string') {
    return (status as { state: string }).state;
  }
  return '';
}

/** 首个非空字符串（审批交互身份的候选键序读取） */
function firstStrOf(...candidates: unknown[]): string {
  for (const c of candidates) {
    if (typeof c === 'string' && c !== '') return c;
  }
  return '';
}

/**
 * v3 decision('allow-once'|'deny') → engine resolution（per interaction kind）。
 * 引擎词汇表（事实源）：
 *   exec        decision ∈ 'allow-once'|'allow-always'|'deny'（gateway
 *               resolve.ts validDecisions）——v3 两值恰在表内，直传；
 *   mode_switch decision ∈ 'proceed'|'stay'（同一 gateway 口径）——
 *               allow-once→proceed / deny→stay；
 *   ask_user    decision ∈ 'submit'|'cancel'，submit 需结构化答案载荷
 *               （answer/answers/values/selected_options，router
 *               `_validated_resolution`）——v3 decision 枚举零载荷面：
 *               allow-once → null（typed 拒绝，B13+ UI 供答案）；deny →
 *               'cancel'（无载荷正当路径）。
 * kind 缺省 → 'exec'：唯一无 kind 的请求帧是 v3 形 exec.approval.requested
 * （gateway interaction.requested 三类都显式带 kind）。
 * 未知 kind（引擎新词汇）：原样透传，合法性与 422 由引擎裁决——pending 保持。
 */
function buildResolveResolution(
  kind: string | undefined, decision: 'allow-once' | 'deny',
): Record<string, unknown> | null {
  switch (kind ?? 'exec') {
    case 'exec':
      return { kind: 'exec', decision };
    case 'mode_switch':
      return { kind: 'mode_switch', decision: decision === 'allow-once' ? 'proceed' : 'stay' };
    case 'ask_user':
      return decision === 'deny'
        ? { kind: 'ask_user', decision: 'cancel' }
        : null;                         // submit 无答案载荷面 → typed 拒绝
    default:
      return { kind, decision };        // 未知 kind 透传（引擎裁决）
  }
}

/** 引擎 GetTask artifacts 的最新全量文本（[...latest_text...]——自后向前取首个非空） */
function latestArtifactText(task: Record<string, unknown>): string | undefined {
  const artifacts = Array.isArray(task.artifacts) ? task.artifacts : [];
  for (let i = artifacts.length - 1; i >= 0; i -= 1) {
    const a = artifacts[i];
    if (typeof a !== 'object' || a === null || Array.isArray(a)) continue;
    const parts = Array.isArray((a as Record<string, unknown>).parts)
      ? (a as Record<string, unknown>).parts as unknown[] : [];
    const text = parts.map((p) =>
      (typeof p === 'object' && p !== null
        && typeof (p as { text?: unknown }).text === 'string')
        ? (p as { text: string }).text : '').join('');
    if (text !== '') return text;
  }
  return undefined;
}

/** wire FAILED 终态 metadata → typed failure（errorCode/errorMessage 事实源） */
function buildRunFailure(metadata: Record<string, unknown>): A2aRunFailure {
  const errorCode = typeof metadata.errorCode === 'string' ? metadata.errorCode : undefined;
  const errorMessage = typeof metadata.errorMessage === 'string' ? metadata.errorMessage : undefined;
  return {
    kind: 'rpc',
    reason: 'run_failed',
    ...(errorCode !== undefined ? { errorCode } : {}),
    rpcError: {
      code: RUN_FAILED_RPC_CODE,
      message: errorMessage ?? 'a2a run failed',
    },
  };
}

export class A2AProvider {
  // ─── SDK 契约回调（ChatProvider drop-in 面） ─────────────────────────
  onMessage?: (message: A2aChatMessage) => void;
  onComplete?: (messages: A2aChatMessage[]) => void;
  onError?: (error: Error) => void;
  /** A2A 面无传输级 auto-reconnect（run 级恢复属 B11）——字段保留保 drop-in */
  onReconnectFailed?: () => void;
  /** B12 取消四态播报（可选 prop，无 SDK 违约）：CancelTask 控制面每次落地
   * （含 provider 已 teardown 的迟到响应——§3.1 迟到核销不得丢上行）回一次。
   * UI 文案单源：CANCEL_UI_TEXT[outcome.kind]；pre-task abort 播 clean
   * （服务端零 run，无"底层执行停止未确认"面）。 */
  onCancelOutcome?: (outcome: CancelOutcome) => void;

  private readonly config: A2AProviderConfig;
  /** B12 context 级 send-ban（config 注入或模块级缺省——§3.1 生命周期独立） */
  private readonly sendBan: SendBanRegistry;
  private parser: SdkParser | null = null;
  private readonly parserReady: Promise<void>;
  private connected = false;
  /** 迟帧栅栏计数：abort/disconnect/新 request 各自 ++ */
  private generation = 0;
  private currentAbort: AbortController | null = null;
  private isRequesting = false;
  private currentMessageId = '';
  private activeSessionKey = '';
  /** B11 恢复游标锚：最近一次已消费 wire seq */
  private appliedSeq: number | null = null;
  /** B11 手工恢复锚：最近 run 的 (taskId, sessionKey, 滚动全文 bag) */
  private recoveryAnchor:
    | { taskRunId: string; sessionKey: string; state: { fullText: string } }
    | null = null;
  /** 终帧/错误帧是否已入账（manualRecover 的"无事可恢复"判据；request 开局清零） */
  private runSettled = false;
  private parseResultListeners: Array<(result: SdkParseResult) => void> = [];
  private connectionStatusListeners:
    Array<(event: A2aConnectionStatusEvent) => void> = [];

  constructor(config: A2AProviderConfig) {
    this.config = config;
    this.sendBan = config.sendBan ?? DEFAULT_SEND_BAN_REGISTRY;
    this.parserReady = loadOpenClawParser().then((Ctor) => {
      const parser = new Ctor({
        enableThinking: config.enableThinkingTag,
        xIAMToken: config.xIAMToken,
      });
      this.wireParser(parser);
      this.parser = parser;
    });
    // 防"构造后在首个使用点之前"的 unhandled rejection 噪音（双分支，awaiter 照常拿到拒绝）
    this.parserReady.catch(() => undefined);
  }

  // ─── ChatProvider 接口 ───────────────────────────────────────────────

  /** card 探测 + 分场景能力门，然后才置连接态（B13 factory 捕获此异常回退 v3） */
  async connect(): Promise<void> {
    await this.ensureParser();          // connect 完成即 parser 就绪（后续同步 API 可用）
    this.notifyConnectionStatus({ status: 'connecting', retryCount: 0 });
    const probe = await fetchCard(
      this.config.a2aUrls.cardUrl,
      this.config.fetchImpl ?? fetch,
      { timeoutMs: 5000 } as const,
    );
    if (!probe.card) {
      throw new Error(`a2a probe failed: ${probe.reason ?? 'unknown'}`);
    }
    const failed = validateScenarios(probe.card, [
      Scenario.STREAM, Scenario.RECOVERY,
    ]).filter((r) => !r.ok);
    if (failed.length > 0) {
      throw new Error(
        `a2a probe failed: ${failed.map((r) => `${r.scenario}:${r.reason}`).join(', ')}`);
    }
    // 探测通过：不缓存（TransportChoiceCache 归 B13），本层只管连接面
    this.connected = true;
    this.notifyConnectionStatus({ status: 'connected', retryCount: 0 });
  }

  disconnect(): void {
    this.generation += 1;               // 栅栏：在途旧迭代器随后帧全废
    const wasConnected = this.connected;
    this.currentAbort?.abort();         // M1 语义：断流非失败
    this.isRequesting = false;
    this.connected = false;
    if (wasConnected) {
      // 对位 v3 transport.onClose（OpenClawProvider.js:108-117）：断开不发 onComplete
      this.notifyConnectionStatus({ status: 'disconnected', retryCount: 0 });
    }
  }

  get isConnected(): boolean {
    return this.connected;
  }

  /** B11 恢复游标（applied-cursor）只读锚 */
  get lastAppliedSeq(): number | null {
    return this.appliedSeq;
  }

  /** 本次/最近一次请求的会话键（对位 v3 currentSessionKey，abort/recovery 关联锚） */
  get currentSessionKey(): string {
    return this.activeSessionKey;
  }

  async request(params: A2aOpenClawInput, messageId?: string): Promise<void> {
    const parser = await this.ensureParser();
    if (this.isRequesting) {
      // 对位 OpenClawProvider.js:222-226
      throw new Error('A request is already in progress');
    }
    if (!this.connected) {
      await this.connect();             // 对位 OpenClawProvider.js:228-234
    }

    // 会话键 v3 同源逻辑（|| 链 + 缺省，对位 :243）：parser 与合成帧同一值，
    // includes 门等值必过（B9 钉板 #2）
    const sessionKey = params.sessionKey || this.config.sessionKey
      || DEFAULT_SESSION_KEY;
    this.isRequesting = true;
    this.currentMessageId = messageId ?? newMessageId();
    this.activeSessionKey = sessionKey;
    const generation = ++this.generation;   // 本代请求车票：一切旧迭代器帧由此废
    const ctrl = new AbortController();
    this.currentAbort = ctrl;
    this.appliedSeq = null;
    this.recoveryAnchor = null;        // 恢复锚随游标一起重置（首 Task 类事件重建）
    parser.reset();                    // 对位 :245
    parser.setSessionKey(sessionKey);  // 对位 :246

    const rpcRequest = buildRpcRequest('SendStreamingMessage', {
      message: {
        messageId: this.currentMessageId,
        contextId: sessionKey,
        parts: [{ text: params.query }],
      },
    });
    // B10 消费面 = query 主 run（attachments/isInject 的独立作用域面归 B11+）；
    // B11 起：主回路与恢复轮共用同一 RunOutcomeBag 记账（consumeWireEvent 单管线）
    const state = { fullText: '' };
    const ctx: TranslateCtx = { sessionKey, taskRunId: null };
    this.runSettled = false;
    const bag: RunOutcomeBag = {
      parser, ctx, state,
      outcome: null, runFailure: null, terminalApplied: false,
    };
    const iamToken = await this.resolveIamToken();

    try {
      for await (const ev of streamRpc(this.config.a2aUrls.rpcUrl, rpcRequest, {
        iamToken, signal: ctrl.signal,
        fetchImpl: this.config.fetchImpl ?? undefined,
      })) {
        if (generation !== this.generation) break;   // 迟帧栅栏
        const step = this.consumeWireEvent(ev, bag);
        if (step !== 'cont') break;    // 终态后关流——此处 break 同义，不必读流尾
      }
    } catch (err) {
      // abort()/disconnect()/新 request 让位：M1 语义（主动取消非失败），静默收尾
      // （UI 完成播报已在 abort() 的 onComplete([]) 发过）
      if (generation !== this.generation) {
        this.isRequesting = false;
        return;
      }
      const typed = (err as { failure?: WireFailure }).failure;
      if (typed && ctx.taskRunId === null) {
        this.isRequesting = false;
        // B13：wire 级失败先播报给宿主（status:'error' 携带原始 typed failure，
        // 传输选择锁 auth/protocol → locked-v3 由此订阅分类——分类逻辑归宿主，
        // 本层不内嵌传输选择）；再走结果未知收口（.failure 重分类不可丢在前）。
        this.notifyHardFailure(err);
        // spec §3 #2：首 Task 事件前断流 = 结果未知——永不自动恢复、永不自动
        // 重发（零后续引擎调用）；typed prior_to_task_event + 结果未知信号语
        const error = Object.assign(new Error(OUTCOME_UNKNOWN_MESSAGE), {
          failure: { kind: 'transport', reason: 'prior_to_task_event' } as WireFailure,
        });
        this.onError?.(error);
        throw error;
      }
      if (typed && bag.outcome === null) {
        // B11 spec §3 #1：known-taskId 断流按已消费游标自动恢复（ReplayEvents）。
        // ⚠️ 恢复期仍持请求槽：request() 尚未收尾，abort() 的 in-flight 门必须
        // 视之为在途（onComplete([]) 播报语义完整）；让位才放槽。
        const recovered = await this.runRecovery(generation, bag, ctrl);
        if (generation !== this.generation) return;   // 恢复中让位：槽归让位方
        this.isRequesting = false;
        this.handleRecoveryOutcome(bag, recovered);
        // resumed/terminal → 正常收账（FAILED 走正常错误路径）
        this.settleRun(bag);
        return;
      }
      // 传输/协议/鉴权 typed failure（含恢复后仍不可收账的裸失败面）：
      // 直接 onError + 原样重抛（.failure 随行）。连接面不因单次断流掉线
      // （run 级恢复=B11）——与 v3 的 ws 面语义差异在此声明。
      this.isRequesting = false;
      this.notifyHardFailure(err);   // B13：同上——wire 级失败先对宿主播报
      const error = err instanceof Error ? err : new Error(String(err));
      this.onError?.(error);
      throw error;
    }

    this.isRequesting = false;
    if (generation !== this.generation) {
      return;                     // 被让位的旧 run：静默 resolve，不动 UI
    }
    this.settleRun(bag);
    // completed / resumed：parser.onComplete 钩子已驱动 UI（钉板 #7）；request 就此 resolve
  }

  abort(): void {
    this.generation += 1;               // 栅栏：旧迭代器逃逸帧拦截
    const inFlight = this.isRequesting;
    this.currentAbort?.abort();         // M1 语义：断流=非失败（streamRpc 静默 return）
    this.isRequesting = false;
    if (!inFlight) {
      return;                           // 空闲 abort 纯 no-op（B10 钉：不二发 onComplete）
    }
    const anchor = this.recoveryAnchor;
    if (anchor) {
      // known-taskId 在途（含恢复轮——request 槽在恢复期仍持有，同走此路）：
      // dispatch wire CancelTask + 乐观挂禁；控制面落地后核销（§3.1）。
      this.fireWireCancel(anchor.taskRunId, anchor.sessionKey);
    } else {
      // pre-task abort：服务端尚无 run（首 Task 类事件未到）——零 wire 面，
      // 无"底层执行"可停；clean 终局播报，无禁令。
      this.onCancelOutcome?.({ kind: 'cancelled-clean' });
    }
    // v3 钉板 OpenClawProvider.js:291-299：abort = _isRequesting=false +
    // onComplete([])，无 onError——UX 即时完成播报语义不变（任务钉板）。
    this.onComplete?.([]);
  }

  // ─── B12 CancelTask 控制面（§4.3 四态机 + §3.1 context send-ban） ─────────

  /**
   * dispatch 即乐观挂禁 + fire-and-forget 控制面：UI 承诺（v3 abort 同步
   * 返回）不等待 CancelTask；in-flight 控制面 promise 落地后核销禁令与
   * onCancelOutcome——§3.1 迟到规则：核销不受 generation/teardown 影响
   * （registry 是 context 级、provider 无关；断连/换代后仍写）。
   * 重复 CancelTask 不是解禁 workaround（引擎 task_terminal 不再发 abort）——
   * 本层只此一处发 CancelTask，无重试。
   */
  private fireWireCancel(taskId: string, contextId: string): void {
    this.sendBan.begin(this.config.a2aUrls.rpcUrl, contextId);
    void this.runCancelControl(taskId, contextId);
  }

  /** 单次 CancelTask 控制面：成功/typed 失败/超时全部折算进 CancelOutcome。 */
  private async runCancelControl(taskId: string, contextId: string): Promise<void> {
    let outcome: CancelOutcome;
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), CANCEL_RPC_TIMEOUT_MS);
      try {
        const parsed = await rpcOnce(
          this.config.a2aUrls.rpcUrl,
          buildRpcRequest('CancelTask', { taskId }),
          {
            iamToken: await this.resolveIamToken(),
            signal: ctrl.signal,
            fetchImpl: this.config.fetchImpl ?? undefined,
          },
        );
        outcome = classifyCancel({ ok: true, result: parsed.result });
      } finally {
        clearTimeout(timer);
      }
    } catch (err) {
      const failure = (err as { failure?: WireFailure }).failure;
      outcome = classifyCancel({
        rpcError: failure?.rpcError,
        ...(failure ? { failure } : {}),
      });
    }
    // 迟到核销（§3.1）：无 generation 门——teardown 后本写必须仍然发生
    this.applyCancelBanDisposition(outcome, contextId);
    this.onCancelOutcome?.(outcome);
    // F8（§4.3 row 4 文案承诺）：cancel-unknown 才单发 GetTask 探测，toast 文案
    // 不变（onCancelOutcome 已按 unknown 播报，探测不追加 UI 事件）
    if (outcome.kind === 'cancel-unknown') {
      void this.probeUnknownCancel(taskId, contextId);
    }
  }

  /**
   * 四态 → 禁令核销表（§3.1，测试逐行钉）：
   *   cancelled-clean         → releaseOnConfirmedAbort（唯自动解禁位）
   *   cancelled-unconfirmed   → begin 保持挂禁
   *   cancel-unknown          → begin 保持挂禁（乐观位在，续挂即幂等）
   *                            + F8：单发 best-effort GetTask 探测（§4.3 row 4 文案
   *                              承诺的"尝试查询任务状态"；探得非 CANCELED 终态
   *                              才按 releaseOnTerminal 解禁，其余结局禁令不动）
   *   cancel-rejected         → releaseOnTerminal（状态已知：run 按 UI 已知
   *                             状态继续/已终局，无"被取消的底层执行"悬空面）
   *   cancel-rejected-unknown → begin 保持挂禁（F2/§3.1：GetTask 查无任务/
   *                             not_found 均不构成底层执行已停止的证明，不解除
   *                             禁发——与 cancel-rejected 同文案，但禁令核销行
   *                             不同，typed 分行防误放行）
   */
  private applyCancelBanDisposition(outcome: CancelOutcome, contextId: string): void {
    const rpcUrl = this.config.a2aUrls.rpcUrl;
    switch (outcome.kind) {
      case 'cancelled-clean':
        this.sendBan.releaseOnConfirmedAbort(rpcUrl, contextId);
        break;
      case 'cancelled-unconfirmed':
      case 'cancel-unknown':
      case 'cancel-rejected-unknown':
        this.sendBan.begin(rpcUrl, contextId);
        break;
      case 'cancel-rejected':
        this.sendBan.releaseOnTerminal(rpcUrl, contextId);
        break;
    }
  }

  /**
   * F8：cancel-unknown 的 GetTask 探测（§4.3 row 4 文案承诺的落地）。单发
   * best-effort：任何失败静默（探测失败不产新结局——禁令照 §3.1 保持）。
   * 只认非 CANCELED 终态（COMPLETED/FAILED）解禁——CANCELED 永不走
   * releaseOnTerminal（解禁只认 engineAbort=confirmed）。迟到语义同核销位：
   * 无 generation 门，teardown 后释放照写。
   */
  private async probeUnknownCancel(taskId: string, contextId: string): Promise<void> {
    try {
      const parsed = await rpcOnce(
        this.config.a2aUrls.rpcUrl,
        buildRpcRequest('GetTask', { taskId }),
        {
          iamToken: await this.resolveIamToken(),
          fetchImpl: this.config.fetchImpl ?? undefined,
        },
      );
      const task = (parsed.result as Record<string, unknown> | undefined)?.task;
      if (typeof task !== 'object' || task === null) return;
      const state = terminalStateOf(task as Record<string, unknown>);
      if (state === STATE_COMPLETED || state === STATE_FAILED) {
        this.sendBan.releaseOnTerminal(this.config.a2aUrls.rpcUrl, contextId);
      }
    } catch {
      /* best-effort：无新证据即不解禁（§3.1 无停止证明不释放） */
    }
  }

  /** §3.1 非 cancel 终态解禁（COMPLETED/FAILED；GetTask 确认同路）——
   *  CANCELED 终态不走此路（解禁只认 engineAbort=confirmed）。 */
  private releaseBanOnNonCancelTerminal(contextId: string): void {
    this.sendBan.releaseOnTerminal(this.config.a2aUrls.rpcUrl, contextId);
  }

  // ─── B11 恢复面（自动恢复在 request() catch 内联；本节为公共锚点） ────

  /**
   * 手工恢复入口（B13/UI 接线；B11 只立 API 不进 UI）。
   * 开一轮**只读**恢复（不做 execute 重发）：fresh per-round 预算（spec：手工轮
   * 自成预算），事件走同一 translate→parser 管线与代际栅栏。
   * null = 无事可恢复（无 taskId / 已终局 / 有在途请求）或恢复中被让位
   * （新请求/abort/disconnect——UI 播报归让位方，本层静默废止）。
   */
  async manualRecover(): Promise<RecoveryOutcome | null> {
    const anchor = this.recoveryAnchor;
    if (!anchor || this.runSettled || this.isRequesting) return null;
    const parser = await this.ensureParser();
    const generation = this.generation;   // 当前代即取消锚
    const ctrl = new AbortController();
    this.currentAbort = ctrl;             // 手工轮也接 abort/disconnect 断流线
    const bag: RunOutcomeBag = {
      parser,
      ctx: { sessionKey: anchor.sessionKey, taskRunId: anchor.taskRunId },
      state: anchor.state,
      outcome: null, runFailure: null, terminalApplied: false,
    };
    const runner = new RecoveryRunner(
      this.buildRecoveryEngine(ctrl), this.config.recovery);
    const recovered = await runner.run(
      anchor.taskRunId, this.appliedSeq,
      (ev) => {
        if (generation !== this.generation) return;   // 迟帧栅栏：手工轮同门
        this.consumeWireEvent(ev, bag);
      },
      { isCancelled: () => generation !== this.generation },
    );
    if (recovered.outcome === 'cancelled') return null;
    if (recovered.outcome === 'resumed-incomplete') {
      this.applyRecoveredIncomplete(bag, recovered);
      this.onError?.(new Error(RECOVERY_INCOMPLETE_MESSAGE));
      return recovered;
    }
    if (recovered.outcome === 'unknown') {
      this.onError?.(new Error(OUTCOME_UNKNOWN_MESSAGE));
    } else if (recovered.outcome === 'budget-exhausted') {
      this.onError?.(new Error(RECOVERY_STOPPED_MESSAGE));
    } else if (recovered.outcome === 'fatal') {
      // F4：手工轮同路——硬失败播报宿主（锁检测）+ 预算耗尽等价信号语
      this.notifyFatalRecovery(recovered.failure);
      this.onError?.(new Error(RECOVERY_STOPPED_MESSAGE));
    }
    // terminal/resumed：consumeWireEvent 已喂 parser，UI 面由 parser 钩子驱动
    return recovered;
  }

  // ─── 审批（B12：ResolveInteraction wire 面 + 两步 parser 释放） ──────────

  /**
   * 同 run 决策提交（spec §5/引擎 E4 §8.6）：
   *  1. 交互身份：pending.approvalData 的 interactionId 优先（gateway 侧
   *     真实标识，engine resolve 注册表按它寻址——router `_forward_resolve`
   *     docstring 钉死不得换成 kernel rid/native run id），回退 id/approvalId；
   *  2. v3 decision('allow-once'|'deny') → engine resolution per kind
   *     （映射表见 buildResolveResolution 头注）；ask_user 的 allow 面 typed
   *     拒绝（无答案载荷：B13+ UI 供 payload 后放开）；
   *  3. wire：rpcOnce('ResolveInteraction')；error data.reason==='resolve_unsupported'
   *     → typed A2A_APPROVAL_UNSUPPORTED_MESSAGE 且 pending 保持（引擎未
   *     接受决策：不假完成、不挂死）；其余失败原样上浮、pending 同样保持；
   *  4. 引擎接受 → B8 两步 parser 释放（OpenClawProvider.js:305-329 同形）：
   *     updateApprovalStatus + clearPendingApproval；allow 面按序重喂被阻塞帧。
   */
  async resolveApproval(id: string, decision: 'allow-once' | 'deny'): Promise<void> {
    const parser = await this.ensureParser();
    const pending = parser.getPendingApproval();
    if (!pending) {
      throw new Error('a2a approval not pending');
    }
    // 身份匹配：parser 的 approvalId 源自 v3 形 payload.id；gateway 形帧（interaction.requested）
    // 带 interactionId 而无 id——两键都认，防"UI 拿引擎侧 id 调用"错配
    const data = pending.approvalData ?? {};
    const interactionId = firstStrOf(data.interactionId, data.id, pending.approvalId, id);
    if (interactionId !== id && ![pending.approvalId, data.interactionId, data.id].includes(id)) {
      throw new Error(`a2a approval mismatch: ${id}`);
    }
    const resolution = buildResolveResolution(
      typeof data.kind === 'string' ? data.kind : undefined, decision);
    if (resolution === null) {
      const error = Object.assign(
        new Error(A2A_APPROVAL_ANSWERS_REQUIRED_MESSAGE), {
          failure: { kind: 'protocol', reason: 'approval_answers_required' } as A2aApprovalFailure,
        });
      // ask_user allow 面：pending 保持（typed 拒绝，不做任何 parser 写）
      throw error;
    }
    try {
      await rpcOnce(
        this.config.a2aUrls.rpcUrl,
        buildRpcRequest('ResolveInteraction', { interactionId, resolution }),
        {
          iamToken: await this.resolveIamToken(),
          fetchImpl: this.config.fetchImpl ?? undefined,
        },
      );
    } catch (err) {
      const failure = (err as { failure?: WireFailure }).failure;
      const reason = failure?.rpcError?.data
        ? (failure.rpcError.data as { reason?: unknown }).reason : undefined;
      if (reason === 'resolve_unsupported') {
        // §5 声明≠实现（如 OpenClaw 形态）：决策未被引擎接受——pending 保持
        // （kind=protocol：客户端造词 typed 拒绝，与 ask_user answers-required
        // 同桶；rpcError 原样附录供 UI 分诊）
        const error = Object.assign(
          new Error(A2A_APPROVAL_UNSUPPORTED_MESSAGE), {
            failure: {
              kind: 'protocol', reason: 'approval_unsupported', rpcError: failure!.rpcError,
            } as A2aApprovalFailure,
          });
        throw error;
      }
      throw err;                        // 其余 typed 上浮；两步释放未执行（pending 保持）
    }
    // 引擎接受：两步 parser 释放（B8 钉板：帧释放不了审批，只此 API 能）
    const status = decision === 'allow-once' ? 'approved' : 'rejected';
    parser.updateApprovalStatus(id, status);
    const result = parser.getResult();
    this.onMessage?.(this.resultToMessage(result));   // v3 同形：状态更新播报
    const blockedMessages = parser.clearPendingApproval();
    if (decision === 'allow-once') {
      // v3 同形：只 allow 面重喂被阻塞帧（deny 面丢弃——run 将按拒绝走）
      for (const msg of blockedMessages) {
        parser.handleMessage(msg);
      }
    }
  }

  getPendingApproval(): SdkPendingApproval | null {
    // 透传 parser 公共 pending 机制（OpenClawParser.d.ts:138-143）
    return this.parser?.getPendingApproval() ?? null;
  }

  // ─── 状态订阅（对位 OpenClawProvider.js:380-447） ─────────────────────

  subscribeToParseResult(listener: (result: SdkParseResult) => void): () => void {
    this.parseResultListeners.push(listener);
    return () => {
      this.parseResultListeners = this.parseResultListeners.filter((l) => l !== listener);
    };
  }

  getParseResult(): SdkParseResult {
    return this.parser ? this.parser.getResult() : EMPTY_PARSE_RESULT;
  }

  subscribeToConnectionStatus(
    listener: (event: A2aConnectionStatusEvent) => void,
  ): () => void {
    this.connectionStatusListeners.push(listener);
    return () => {
      this.connectionStatusListeners =
        this.connectionStatusListeners.filter((l) => l !== listener);
    };
  }

  // ─── 内部 ────────────────────────────────────────────────────────────

  /**
   * B11 事件消费单管线（主回路 / 恢复轮 / 手工轮共用）：
   *  · 首 Task 类事件记 taskRunId + 手工恢复锚；
   *  · 游标去重：seq 不越过已消费游标的事件（replay 重发面）不再入账——
   *    唯一豁免是 spec §3.4 终态 marker（与 state-change 共 seq，见下方注释）；
   *  · 双终态防重入：同 run 第二个 final/error 帧只推游标不喂 parser（"no
   *    double final" 核销位——§3.4 豁免后仍不二发）；
   *  · 返回 'final' | 'error' 表示 run 已到收账面（调用方截流）。
   */
  private consumeWireEvent(ev: WireEvent, bag: RunOutcomeBag): 'final' | 'error' | 'cont' {
    if (bag.ctx.taskRunId === null) {
      // wire task 成员带 id，其余成员带 taskId（同一 run id）——首帧即记
      const candidate = ev.type === 'task'
        ? (ev.member as { id?: unknown }).id
        : (ev.member as { taskId?: unknown }).taskId;
      if (typeof candidate === 'string' && candidate) {
        bag.ctx.taskRunId = candidate;
        this.recoveryAnchor = {
          taskRunId: candidate, sessionKey: bag.ctx.sessionKey, state: bag.state,
        };
      }
    }
    // spec §3.4：「同一 seq 上未应用的 final marker 必须应用」。引擎事实
    // （kernel.complete_run，kernel.py:156-180）：终态两连发——state-change
    // statusUpdate(COMPLETED) 先经 _publish 把 last_seq 推到 N+1 并消费（游标
    // →N+1），随后 final marker 以**同一 r.last_seq = N+1** 构造下发——inclusive
    // 去重会把 marker 当重发吞掉（final 永不应用 → settleRun 无账可收 → UI 挂死）。
    // 终态 marker 豁免 `<=` 去重；双终态防重由下方 terminalApplied 栅栏兜（同 run
    // 第二终帧不再入账），主回路/replay 恢复/手工轮同一管线同豁免。
    const isTerminalMarker = ev.type === 'statusUpdate' && ev.metadata.final === 'true';
    if (!isTerminalMarker
      && ev.seq !== null && this.appliedSeq !== null && ev.seq <= this.appliedSeq) {
      return 'cont';                // 已消费游标之前的事件（重发面）：纯去重
    }
    const out = translateEvent(ev, bag.ctx, bag.state);
    if (out === null) {
      // 无渲染面（task/message 成员、纯 WORKING 心跳）：游标仍推进
      if (ev.seq !== null) this.appliedSeq = ev.seq;
      return 'cont';
    }
    if ((out.kind === 'final' || out.kind === 'error') && bag.terminalApplied) {
      return 'cont';                // 同 run 第二终帧：防 parser 双 final
    }
    if (out.consumedSeq !== null) this.appliedSeq = out.consumedSeq;
    if (out.kind === 'final' || out.kind === 'error') {
      bag.terminalApplied = true;
      this.runSettled = true;
    }
    bag.parser.handleMessage(out.frame);
    if (out.kind === 'error') {
      // parser.onError 已驱动 UI（钉板 #8）；分完帧再收账
      bag.outcome = 'failed';
      bag.runFailure = buildRunFailure(ev.metadata);
      // §3.1 FAILED = 非 cancel 终态：按正常完成语义解禁（GetTask 确认同路）
      this.releaseBanOnNonCancelTerminal(bag.ctx.sessionKey);
      return 'error';
    }
    if (out.kind === 'final') {
      // ⚠️ B9 契约：CANCELED/REJECTED 也出 final 帧（parser 纯 no-op）——
      // 这里以成员终态区分 completion outcome 与 transport outcome
      const stateStr = terminalStateOf(ev.member);
      bag.outcome = (stateStr === STATE_CANCELED || stateStr === STATE_REJECTED)
        ? 'cancelled' : 'completed';
      if (bag.outcome !== 'cancelled') {
        // §3.1 非 cancel 终态（COMPLETED 等）：正常完成语义解禁——
        // CANCELED 走流也**不**解禁（解禁只认 engineAbort=confirmed）
        this.releaseBanOnNonCancelTerminal(bag.ctx.sessionKey);
      }
      return 'final';
    }
    return 'cont';
  }

  /** run 收账三分支（completed/resumed 的 UI 面由 parser.onComplete 钩子已驱动） */
  private settleRun(bag: RunOutcomeBag): void {
    if (bag.outcome === 'failed' && bag.runFailure) {
      throw Object.assign(new Error(bag.runFailure.rpcError!.message), { failure: bag.runFailure });
    }
    if (bag.outcome === 'cancelled') {
      // v3 钉板：OpenClawProvider.js:291-299 abort() → onComplete([])；
      // parser 对废除帧 no-op（OpenClawParser.js:637），完成播报由本层补发
      this.onComplete?.([]);
    }
  }

  /**
   * 自动恢复轮（request 断流内联调用）：known taskId + 已消费游标 →
   * RecoveryRunner（预算 spec §4.4 首版默认，config.recovery 可覆盖）。
   * 恢复事件与主回路同管线同栅栏（abort/disconnect/新请求即取消 runner）。
   */
  private async runRecovery(
    generation: number, bag: RunOutcomeBag, ctrl: AbortController,
  ): Promise<RecoveryOutcome> {
    const taskId = bag.ctx.taskRunId!;
    const runner = new RecoveryRunner(
      this.buildRecoveryEngine(ctrl), this.config.recovery);
    return runner.run(
      taskId, this.appliedSeq,
      (ev) => {
        if (generation !== this.generation) return;   // 迟帧栅栏：恢复流同门
        this.consumeWireEvent(ev, bag);
      },
      { isCancelled: () => generation !== this.generation },
    );
  }

  /** 恢复轮终局核销：止损面 → onError 信号语 + typed 拒绝（settleRun 前） */
  private handleRecoveryOutcome(bag: RunOutcomeBag, recovered: RecoveryOutcome): void {
    switch (recovered.outcome) {
      case 'resumed-incomplete':
        this.applyRecoveredIncomplete(bag, recovered);
        throw this.surfaceRecoverySignal('recovery_incomplete', RECOVERY_INCOMPLETE_MESSAGE);
      case 'unknown':
        throw this.surfaceRecoverySignal('recovery_outcome_unknown', OUTCOME_UNKNOWN_MESSAGE);
      case 'budget-exhausted':
        throw this.surfaceRecoverySignal('recovery_stopped', RECOVERY_STOPPED_MESSAGE);
      case 'fatal':
        // F4：恢复期硬失败（auth/protocol）——先向宿主播报（locked-v3 锁检测
        // 起点与 pre-task/请求期同路），再按预算耗尽等价信号语收口（原始
        // failure 经 cause 随行）。恢复中 auth 拒绝不再空烧 4 轮重放。
        throw this.surfaceFatalRecovery(recovered.failure);
      default:
        break;                      // resumed/terminal/cancelled → settleRun 收账
    }
  }

  /**
   * F4：恢复期硬失败（auth/protocol，NonRetryable early-exit）的对宿主播报——
   * 恢复分支此前缺 notifyHardFailure（pre-task 面有，恢复面没有），鉴权拒绝
   * 触发不了 bootstrap 的 lock 监听、还会烧满 5 attempt 预算。此处一次播报
   * （runner 已把 auth/protocol 设为非可重试终局，故每轮 recovery 至多一次）。
   */
  private notifyFatalRecovery(failure: WireFailure): void {
    this.notifyHardFailure(Object.assign(new Error('a2a recovery hard failure'), { failure }));
  }

  /** fatal 收口 = 预算耗尽等价信号语 + 原始 failure 附录（cause 随行分诊） */
  private surfaceFatalRecovery(
    failure: WireFailure,
  ): Error & { failure: A2aRecoverySignalFailure } {
    this.notifyFatalRecovery(failure);
    return this.surfaceRecoverySignal('recovery_stopped', RECOVERY_STOPPED_MESSAGE, failure);
  }

  /** 信号语双面：onError 上浮 + typed 拒绝（reason 核销表见 A2aRecoverySignalFailure）
   *  cause 仅 fatal 收口随行（budget-equivalent 信号的原始失败附录）。 */
  private surfaceRecoverySignal(
    reason: A2aRecoverySignalFailure['reason'], message: string,
    cause?: WireFailure,
  ): Error & { failure: A2aRecoverySignalFailure } {
    const error = Object.assign(new Error(message), {
      failure: {
        kind: 'rpc', reason,
        ...(cause !== undefined ? { cause } : {}),
      } as A2aRecoverySignalFailure,
    });
    this.onError?.(error);
    return error;
  }

  /**
   * 'resumed-incomplete' 的结果取回（recovery.ts 不伪造 wire 事件——帧合成归本层）：
   * COMPLETED → 按 B9 chatFrame 同构合成 final 全文快照帧喂 parser（结果可取回，
   * parser.onComplete 驱动展示）；取消/拒绝/失败终态无安全合成正断面——只标不完整。
   */
  private applyRecoveredIncomplete(
    bag: RunOutcomeBag, outcome: { state?: string; latestText?: string },
  ): void {
    if (outcome.state !== STATE_COMPLETED) return;
    if (bag.terminalApplied) return;
    bag.terminalApplied = true;
    bag.outcome = 'completed';
    this.runSettled = true;
    // §3.1 GetTask 确认的 COMPLETED 终态：进非 cancel 终态解禁流
    this.releaseBanOnNonCancelTerminal(bag.ctx.sessionKey);
    const text = outcome.latestText ?? bag.state.fullText;
    const frame = {
      type: 'event',
      event: 'chat',
      seq: null,
      payload: {
        runId: bag.ctx.taskRunId ?? 'a2a-run',
        sessionKey: bag.ctx.sessionKey,          // 钉板 #2：同值过 includes 门
        request: { sessionKey: bag.ctx.sessionKey },   // 钉板 #4：request REQUIRED
        seq: null,
        state: 'final',
        message: { role: 'assistant', content: [{ type: 'text', text }] },
      },
    };
    bag.parser.handleMessage(frame);
  }

  /** 恢复引擎 adapter：streamRpc/rpcOnce over 实例面（iamToken 每调取新，signal 共请求 Ctrl） */
  private buildRecoveryEngine(ctrl: AbortController): RecoveryEngine {
    const rpcUrl = this.config.a2aUrls.rpcUrl;
    const fetchImpl = this.config.fetchImpl ?? undefined;
    const resolveToken = () => this.resolveIamToken();
    return {
      // spec §3 #1：known-taskId 断流一律按已消费游标 ReplayEvents
      //（backlog 先回放后 live；拒绝以 RPC error 信封 typed 上浮）
      replay: async function* replay(taskId: string, sinceSeq: number) {
        const iamToken = await resolveToken();
        yield* streamRpc(
          rpcUrl, buildRpcRequest('ReplayEvents', { taskId, sinceSeq }),
          { iamToken, signal: ctrl.signal, fetchImpl });
      },
      // GetTask 竞态分支的唯一合法订阅位（kernel sinceSeq=None 空 backlog）
      subscribeFuture: async function* subscribeFuture(taskId: string) {
        const iamToken = await resolveToken();
        yield* streamRpc(
          rpcUrl, buildRpcRequest('SubscribeToTask', { taskId, sinceSeq: null }),
          { iamToken, signal: ctrl.signal, fetchImpl });
      },
      // 单信封 GetTask：not_found_or_expired → {found:false}（引擎契约）
      getTask: async (taskId: string) => {
        try {
          const parsed = await rpcOnce(
            rpcUrl, buildRpcRequest('GetTask', { taskId }),
            { iamToken: await resolveToken(), signal: ctrl.signal, fetchImpl });
          const task = (parsed.result as Record<string, unknown> | undefined)?.task;
          if (typeof task !== 'object' || task === null || Array.isArray(task)) {
            throw Object.assign(new Error('a2a unexpected result shape'), {
              failure: { kind: 'protocol', reason: 'unexpected_result_shape' } as WireFailure,
            });
          }
          const taskRecord = task as Record<string, unknown>;
          const status = taskRecord.status;
          const state = (typeof status === 'object' && status !== null
            && typeof (status as { state?: unknown }).state === 'string')
            ? (status as { state: string }).state : undefined;
          return { found: true, state, latestText: latestArtifactText(taskRecord) };
        } catch (err) {
          const failure = (err as { failure?: WireFailure }).failure;
          const reason = (failure?.rpcError?.data as { reason?: unknown } | undefined)?.reason;
          if (failure?.kind === 'rpc' && reason === 'not_found_or_expired') {
            return { found: false };
          }
          throw err;
        }
      },
    };
  }

  private async ensureParser(): Promise<SdkParser> {
    await this.parserReady;
    return this.parser!;
  }

  private async resolveIamToken(): Promise<string | undefined> {
    if (this.config.iamTokenGetter) {
      return (await this.config.iamTokenGetter()) ?? this.config.xIAMToken;
    }
    return this.config.xIAMToken;
  }

  private notifyParseResult(result: SdkParseResult): void {
    // v3 同款 listener 隔离（iterator.e 吞错不传染遍）
    for (const listener of [...this.parseResultListeners]) {
      try { listener(result); } catch { /* 镜像 v3：单 listener 异常不中断其余 */ }
    }
  }

  private notifyConnectionStatus(event: A2aConnectionStatusEvent): void {
    for (const listener of [...this.connectionStatusListeners]) {
      try { listener(event); } catch { /* 同上 */ }
    }
  }

  /**
   * B13：wire 级失败的对宿主播报（status:'error'，对位 v3 transport.onError 的
   * 观察面）。事件携带原始错误对象——typed failure 的 `.failure` 随行；宿主
   * （bootstrap.ts）按 LOCK_CANDIDATE_KINDS(auth/protocol) 分类收敛为传输选择
   * 锁 locked-v3（spec §3.3）。本层只播报不分类：协议/鉴权/transport 同路
   * 上报，是否锁定由宿主裁决。
   */
  private notifyHardFailure(err: unknown): void {
    this.notifyConnectionStatus({
      status: 'error',
      retryCount: 0,
      ...(err instanceof Error ? { error: err } : {}),
    });
  }

  /**
   * parser 回调桥（对位 OpenClawProvider.js:136-172 的接线，行为逐条镜像）。
   */
  private wireParser(parser: SdkParser): void {
    parser.onUpdate = (result) => {
      this.onMessage?.(this.resultToMessage(result));
      this.notifyParseResult(result);
    };
    parser.onComplete = (result) => {
      this.isRequesting = false;
      this.onComplete?.([this.resultToMessage(result)]);
      this.notifyParseResult(result);
    };
    parser.onError = (err) => {
      this.isRequesting = false;
      // 镜像 OpenClawProvider.js:154-172：错误消息先 onMessage（AbortError → aborted 态）
      const errorMessage = err.message || this.config.fallbackMessage
        || 'Unknown error';
      this.onMessage?.({
        id: this.currentMessageId,
        role: 'assistant',
        content: errorMessage,
        status: err.name === 'AbortError' ? 'aborted' : 'error',
        blocks: [{ type: 'text', content: errorMessage }],
      });
      this.onError?.(err);
    };
  }

  /** ParseResult → ChatMessage（镜像 OpenClawProvider.js:459-479） */
  private resultToMessage(result: SdkParseResult): A2aChatMessage {
    const isInject = result.runId?.startsWith('inject-') ?? false;
    const message: A2aChatMessage = {
      id: isInject ? (result.runId as string) : this.currentMessageId,
      role: 'assistant',
      content: result.content,
      blocks: result.blocks,
      status: result.done ? 'done' : 'streaming',
      sessionKey: result.sessionKey,
    };
    if (result.isApproval && result.approvalRequest) {
      message.extra = {
        isApproval: true,
        approvalRequest: result.approvalRequest,
      };
    }
    return message;
  }
}

/** 主回路 / 恢复轮 / 手工轮共用的 run 记账袋（B11 起单管线入账） */
interface RunOutcomeBag {
  parser: SdkParser;
  ctx: TranslateCtx;
  /** B9 契约：artifactUpdate 独占改写的滚动全文（跨恢复轮存活） */
  state: { fullText: string };
  outcome: 'completed' | 'cancelled' | 'failed' | null;
  runFailure: A2aRunFailure | null;
  /** 双终态防重入（replay 重发面/竞态补看，"no double final" 核销位） */
  terminalApplied: boolean;
}

