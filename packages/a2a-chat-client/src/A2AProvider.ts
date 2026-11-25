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
 *     onCancelOutcome 上行 UI；pre-task abort 分界按出站与否：未出站=服务端
 *     零 run（clean）；已出站首帧前=结果未知 task-outcome-unknown（§3 #2）。
 *   · resolveApproval 真接入：ResolveInteraction 单信封 + 两步 parser 释放；
 *     resolve_unsupported → typed 拒绝、pending 保持（§5）。
 */
import { buildRpcRequest, type WireFailure } from './rpc';
import {
  EMPTY_PARSE_RESULT,
  STATE_COMPLETED, STATE_FAILED,
  buildResolveResolution,
  firstStrOf,
  newMessageId,
  terminalStateOf,
} from './providerFacts';
import { rpcOnce } from './rpcOnce';
import { streamRpc } from './stream';
import { fetchCard, validateScenarios, Scenario } from './card';
import { A2aRunEngine, OUTCOME_UNKNOWN_MESSAGE, RECOVERY_INCOMPLETE_MESSAGE, RECOVERY_STOPPED_MESSAGE, type RunOutcomeBag } from './A2aRunEngine';
import type { TranslateCtx } from './translate';
import {
  RecoveryRunner,
  type RecoveryConfig,
  type RecoveryOutcome,
} from './recovery';
import { classifyCancel, type CancelOutcome } from './cancelOutcomes';
import { createSendBanRegistry, type SendBanRegistry } from './sendBan';
import { scopedSendBanContext } from './policy';
import type { A2aUrls } from './urls';
import {
  A2aParserFactoryMissingError,
  type SdkParserFactory,
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

/** 会话文件引用（resourceReferences/promptFileRefs，REL20260728 §4.4）：v3
 * buildRequest 会把两字段随 chat.send 上 wire（engine ws_server.py 消费）；
 * A2A wire 的 message.parts 只承载文本、引擎 _message_text 无引用消费面——
 * 在字段送入请求前显式拒绝（typed file_refs_unsupported），不静默丢引用。
 * 端到端承载契约（engine A2A 面的引用消费逻辑）落地前，本拒绝是缺能力的
 * 诚实边界；届时本守卫与常量一并移除。 */
export const A2A_FILE_REFS_UNSUPPORTED_MESSAGE =
  '当前 A2A 传输暂不支持会话文件引用，请移除引用后重试';

/** 附件（图片等）：v3 SDK chat.send 会把 attachments 写上 wire（引擎消费
 * 图片面）；A2A 的 message.parts 只有文本承载——静默丢附件会把「分析图片」
 * 降级成纯文本请求、返回错误结果。出站前 typed 拒绝（fail-closed），承载
 * 契约落地前这是缺能力的诚实边界；届时本守卫与常量一并移除。 */
export const A2A_ATTACHMENTS_UNSUPPORTED_MESSAGE =
  '当前 A2A 传输暂不支持发送附件，请移除附件后重试';

/** inject 消息：v3 走 chat.inject（注入语义：入上下文、不计为用户任务）；
 * A2A 只有 SendStreamingMessage 一种语义——照发会把注入当普通执行请求，
 * 可能触发非预期的模型 run。出站前 typed 拒绝，注入承载契约落地前移除。 */
export const A2A_INJECT_UNSUPPORTED_MESSAGE =
  '当前 A2A 传输暂不支持注入消息，请稍后重试';

/** B12：CancelTask 控制面单信封的兜底时限（对位引擎侧 chat.abort 10s RPC 窗；
 * 超时=取消结局不可知 → cancel-unknown 核销，禁令保持）。 */
const CANCEL_RPC_TIMEOUT_MS = 10_000;

/**
 * 模块级缺省 send-ban 注册表：禁令是 context 级状态（键=(rpcUrl, sessionKey)），
 * 与 provider 实例生命周期无关——换代/重建 provider 后同 context 仍见禁令
 * （spec §3.1）。B13 可经 config.sendBan 注入自持实例（测试隔离同路）。
 */
const DEFAULT_SEND_BAN_REGISTRY = createSendBanRegistry();



/**
 * run 级业务失败的归类：B9 事实源——wire FAILED 终态只在 statusUpdate metadata
 * 携 errorCode/errorMessage，没有 JSON-RPC 错误信封。归 rpc 桶（rpc.ts 头注
 * "业务 rpc 错误上浮 UI"），以 JSON-RPC 标准 Internal error 码承载。
 */

/** JSON-RPC Internal error（RFC 早期草案/事实标准码）：run 业务失败的折算承载 */

/** v3 同款缺省会话键（OpenClawProvider.js:243） */
const DEFAULT_SESSION_KEY = 'agent:main:main';

/** engine wire.py 终态口径（与 translate.ts 常量同源） */

/** OpenClawProvider.d.ts:18-27 ConnectionStatusEvent 镜像 */
export interface A2aConnectionStatusEvent {
  status: 'connecting' | 'connected' | 'disconnected' | 'reconnecting' | 'error';
  retryCount: number;
  error?: Error;
  closeCode?: number;
  closeReason?: string;
  wasClean?: boolean;
}



/** B12 审批面 typed 失败标注（protocol 桶=客户端造词的 typed 拒绝，与
 * ask_user answers-required 同构；rpcError 附引擎原文供分诊） */
interface A2aApprovalFailure extends WireFailure {
  reason: 'approval_unsupported' | 'approval_answers_required';
}

export interface A2AProviderConfig extends A2aOpenClawProviderConfig {
  /** A2A 面地址（由 ws url 派生，见 urls.ts） */
  a2aUrls: A2aUrls;
  /** 宿主注入的 parser 工厂（公共/宿主解耦缝）：构造好的 SdkParser 实例——
   * 各宿主提供自家聊天 SDK 的同形适配（nextgen 的开源 SDK 适配在宿主胶水
   * 层，OCB 注内部 SDK 等价物）。必填；缺注入 typed parser_factory_missing 拒。 */
  parserFactory: SdkParserFactory;
  /** 动态 IAM 凭证（每次 request 前取新，覆盖静态 xIAMToken） */
  iamTokenGetter?: () => Promise<string | undefined>;
  /** fetch 注入口（测试 seam；缺省全局 fetch） */
  fetchImpl?: typeof fetch;
  /** 恢复预算覆盖（B11：adapter 级测试 seam；缺省 spec §4.4 首版默认值） */
  recovery?: RecoveryConfig;
  /** B12：context 级 send-ban 注册表（§3.1）。缺省模块级共享实例——禁令
   * 生存期独立于 provider 实例；B13 注入自持实例以复用同一 registry。 */
  sendBan?: SendBanRegistry;
  /** R6：禁令的 bot/runtime 作用域（宿主由 bot+owner+stage 构造，无空格
   * 成分）。挂禁/查询/核销的 context 键统一经它拼域——防止 registry 单例
   * 下不同 bot 的同名 sessionKey 互相串联/误封；缺省（未升级宿主）保持
   * 裸 context 的 legacy 键口径。 */
  sendBanScopeId?: string;
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
  /** R6 挂禁作用域（bot/runtime 键前缀；undefined=legacy 裸 context 键） */
  private readonly sendBanScopeId: string | undefined;
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
  /** B11 手工恢复锚：最近 run 的 (taskId, sessionKey, 滚动全文 bag) */
  /** 终帧/错误帧是否已入账（manualRecover 的"无事可恢复"判据；request 开局清零） */
  /** 已出站的请求代次（该代次的 SendStreamingMessage 已 POST；null=尚无请求出站）。
   * 代次绑定而非布尔：布尔跨请求残留——上一请求置位后，复用 provider 的下一
   * 请求在 iam 签发窗口中止会被误判 task-outcome-unknown（review R2-2）。
   * abort 度线 = "当前在途代次是否曾出站"（dispatchedGeneration === 在途代次）。
   */
  private dispatchedGeneration: number | null = null;
  private readonly run: A2aRunEngine;
  private parseResultListeners: Array<(result: SdkParseResult) => void> = [];
  private connectionStatusListeners:
    Array<(event: A2aConnectionStatusEvent) => void> = [];

  constructor(config: A2AProviderConfig) {
    this.config = config;
    this.sendBan = config.sendBan ?? DEFAULT_SEND_BAN_REGISTRY;
    this.sendBanScopeId = config.sendBanScopeId;
    // parserFactory 注入缝（B10 公共/宿主解耦）：宿主给"构造好的实例工厂"——
    // Promise.resolve().then(...) 推迟调用，兼容同步/异步两种宿主并保住
    // "构造期零同步抛"（对位原 loadOpenClawParser 的 async 恒拒绝语义）：
    // 同步抛/异步拒都折算为 parserReady 拒绝，一切 await 消费点（ensureParser
    // 等）语义零变化。缺注入（js 空洞）→ typed 拒绝，绝不手搓兜底 parser
    //（B8 钉板：真 parser 就是 oracle）。
    this.parserReady = (config.parserFactory
      ? Promise.resolve().then(() => config.parserFactory!({
          enableThinking: config.enableThinkingTag,
          xIAMToken: config.xIAMToken,
        }))
      : Promise.reject(new A2aParserFactoryMissingError())
    ).then((parser) => {
      this.wireParser(parser);
      this.parser = parser;
    });
    // 防"构造后在首个使用点之前"的 unhandled rejection 噪音（双分支，awaiter 照常拿到拒绝）
    this.parserReady.catch(() => undefined);
    // 管线引擎（review 拆分）：host 缝全部 getter/闭包——读实时字段值
    const provider = this;
    this.run = new A2aRunEngine({
      get config() { return provider.config; },
      generation: () => provider.generation,
      resolveIamToken: () => provider.resolveIamToken(),
      notifyHardFailure: (err: unknown) => provider.notifyHardFailure(err),
      releaseBanOnNonCancelTerminal: (cid: string) =>
        provider.releaseBanOnNonCancelTerminal(cid),
      emitCancelledComplete: () => { provider.onComplete?.([]); },
      emitError: (err: Error) => { provider.onError?.(err); },
    });
  }

  // ─── ChatProvider 接口 ───────────────────────────────────────────────

  /**
   * 宿主可识别标记（review R2-3）：发送边界按"当下传输选择"重估——宿主
   * （botChatProvider）据此识别 inner 是否 A2A，对开关关/locked-v3 做新任务
   * 回滚。duck-typing 面而非接口强制（对位 v3 无此成员 = false 语义）。
   */
  readonly isA2aProvider = true as const;

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
      Scenario.STREAM, Scenario.RECOVERY, Scenario.HITL,
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
    return this.run.appliedSeq;
  }

  /** 本次/最近一次请求的会话键（对位 v3 currentSessionKey，abort/recovery 关联锚） */
  get currentSessionKey(): string {
    return this.activeSessionKey;
  }

  async request(params: A2aOpenClawInput, messageId?: string): Promise<void> {
    // 会话文件引用的门（A2A_FILE_REFS_UNSUPPORTED_MESSAGE 头注）：在最前面——
    // 未消费 parser/未占请求槽，拒绝后 provider 全量可用
    const fileRefs: unknown[] = [
      ...(Array.isArray(params.resourceReferences) ? params.resourceReferences : []),
      ...(Array.isArray(params.promptFileRefs) ? params.promptFileRefs : []),
    ];
    if (fileRefs.length > 0) {
      const error = Object.assign(new Error(A2A_FILE_REFS_UNSUPPORTED_MESSAGE), {
        failure: { kind: 'rpc', reason: 'file_refs_unsupported' } as WireFailure,
      });
      this.onError?.(error);
      throw error;
    }
    // 附件与 inject 的门（两常量头注意明理由）：与 fileRefs 同构、同位置——
    // 出站前拒绝，未占请求槽、零 wire 出站（attachments/isInject 的独立
    // wire 承载契约归 B11+，落地前拒绝是唯一诚实行为）
    if (Array.isArray(params.attachments) && params.attachments.length > 0) {
      const error = Object.assign(new Error(A2A_ATTACHMENTS_UNSUPPORTED_MESSAGE), {
        failure: { kind: 'rpc', reason: 'attachments_unsupported' } as WireFailure,
      });
      this.onError?.(error);
      throw error;
    }
    if (params.isInject === true) {
      const error = Object.assign(new Error(A2A_INJECT_UNSUPPORTED_MESSAGE), {
        failure: { kind: 'rpc', reason: 'inject_unsupported' } as WireFailure,
      });
      this.onError?.(error);
      throw error;
    }
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
    this.run.appliedSeq = null;
    this.run.recoveryAnchor = null;        // 恢复锚随游标一起重置（首 Task 类事件重建）
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
    this.run.runSettled = false;
    const bag: RunOutcomeBag = {
      parser, ctx, state,
      outcome: null, runFailure: null, terminalApplied: false,
    };
    // token 获取失败的生命周期边界（review）：此处请求尚未出站——绝不能
    // 走下方发送阶段的 catch（那里会把"首个 Task 事件前的失败"当出站后
    // 断流转成任务结果未知/进恢复语义）；也绝不能因抛错绕过 isRequesting
    // 的归还，否则同 Provider 的后续请求恒"A request is already in
    // progress"。getter 抛错/拒绝：如实上抛（保留 .failure 分类，由宿主
    // 沿 lock 判定规则过滤——不强行标鉴权失败）；让位代次静默回归（不清理
    // 新请求的槽）；abort() 自身也走 resolveIamToken 回归路径由各入口自持
    // 其状态语义，此处不重复清理。
    let iamToken: string | undefined;
    try {
      iamToken = await this.resolveIamToken();
    } catch (err) {
      // 让位代次让 cleanup 归新请求；迟到异常绝不触碰新请求状态
      if (generation !== this.generation) return;
      this.isRequesting = false;
      this.notifyHardFailure(err);
      const error = err instanceof Error ? err : new Error(String(err));
      this.onError?.(error);
      throw error;
    }
    // 等待 token 期间被取消/让位：不再启动发送（abort 的 UI 播报已由 abort
    // 自身完成——不在此重复补发，避免新代次被旧异常窗口污染）
    if (generation !== this.generation) return;
    // 出站位=代次记账（gate 于代次流通：被中止/让位的旧代次不得在延迟归还后
    // 污染新请求的出状态——旧代次的 ctrl 已 abort，其流静默收尾无 POV）
    if (generation === this.generation) {
      this.dispatchedGeneration = generation;
    }

    try {
      for await (const ev of streamRpc(this.config.a2aUrls.rpcUrl, rpcRequest, {
        iamToken, signal: ctrl.signal,
        fetchImpl: this.config.fetchImpl ?? undefined,
      })) {
        if (generation !== this.generation) break;   // 迟帧栅栏
        const step = this.run.consumeWireEvent(ev, bag);
        if (step !== 'cont') break;    // 终态后关流——此处 break 同义，不必读流尾
      }
    } catch (err) {
      // abort()/disconnect()/新 request 让位：M1 语义（主动取消非失败），静默收尾
      // （UI 完成播报已在 abort() 的 onComplete([]) 发过）。
      // ⚠️ 让位子**零实例写**（review R2-1）：请求槽归当前代次（中止方/新请求）
      // 所有——旧代次在此写 isRequesting=false 会清掉新请求的在途态，令新任务
      // 的 abort 被误判空闲 no-op（跳过取消播报与 CancelTask）。
      if (generation !== this.generation) {
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
        const recovered = await this.run.runRecovery(generation, bag, ctrl);
        if (generation !== this.generation) return;   // 恢复中让位：槽归让位方
        this.isRequesting = false;
        this.run.handleRecoveryOutcome(bag, recovered);
        // resumed/terminal → 正常收账（FAILED 走正常错误路径）
        this.run.settleRun(bag);
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

    if (generation !== this.generation) {
      return;                     // 被让位的旧 run：静默 resolve，不动 UI 也不动
    }                             // 请求槽（isRequesting 归中止方/新代所有，R2-1）
    this.isRequesting = false;
    this.run.settleRun(bag);
    // completed / resumed：parser.onComplete 钩子已驱动 UI（钉板 #7）；request 就此 resolve
  }

  abort(): void {
    const inFlightGen = this.generation;  // 在途请求的车票（若有）——本次自增前取值
    const dispatched = this.dispatchedGeneration === inFlightGen;
    this.generation += 1;               // 栅栏：旧迭代器逃逸帧拦截
    const inFlight = this.isRequesting;
    this.currentAbort?.abort();         // M1 语义：断流=非失败（streamRpc 静默 return）
    this.isRequesting = false;
    if (!inFlight) {
      return;                           // 空闲 abort 纯 no-op（B10 钉：不二发 onComplete）
    }
    const anchor = this.run.recoveryAnchor;
    if (anchor) {
      // known-taskId 在途（含恢复轮——request 槽在恢复期仍持有，同走此路）：
      // dispatch wire CancelTask + 乐观挂禁；控制面落地后核销（§3.1）。
      this.fireWireCancel(anchor.taskRunId, anchor.sessionKey);
    } else if (dispatched) {
      // 首帧前中止且请求已出站（**当前在途代次**曾出站——代次绑定防上一请求
      // 残留）：引擎 open_run→dispatch 先于订阅回包（router.py
      // `_send_streaming`），run 可能已受理并执行——但客户端无 taskId，wire
      // 取消无从构造。按 spec §3 #2 报**任务结果未知**（非 clean 谎报已停）；
      // 不挂禁令（无核销锚——GetTask 也要 taskId，挂禁即死锁），用户显式
      // 重发 = 新幂等键（D14 合规路径）。
      this.onCancelOutcome?.({ kind: 'task-outcome-unknown' });
    } else {
      // 出站前中止（iam 签发窗口等）：确定未发送，服务端零 run——
      // 零 wire 面，无"底层执行"可停；clean 终局播报，无禁令。
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
    this.sendBan.begin(this.config.a2aUrls.rpcUrl, this.banContext(contextId));
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
  /** R6：禁令的 context 键（挂禁/核销/宿主查询三口径同键）——scope 前缀
   * 隔离不同 bot 的同名 sessionKey；scope 缺省时保持 legacy 裸键。 */
  private banContext(contextId: string): string {
    return scopedSendBanContext(this.sendBanScopeId, contextId);
  }

  private applyCancelBanDisposition(outcome: CancelOutcome, contextId: string): void {
    const rpcUrl = this.config.a2aUrls.rpcUrl;
    switch (outcome.kind) {
      case 'cancelled-clean':
        this.sendBan.releaseOnConfirmedAbort(rpcUrl, this.banContext(contextId));
        break;
      case 'cancelled-unconfirmed':
      case 'cancel-unknown':
      case 'cancel-rejected-unknown':
        this.sendBan.begin(rpcUrl, this.banContext(contextId));
        break;
      case 'cancel-rejected':
        this.sendBan.releaseOnTerminal(rpcUrl, this.banContext(contextId));
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
        this.sendBan.releaseOnTerminal(this.config.a2aUrls.rpcUrl, this.banContext(contextId));
      }
    } catch {
      /* best-effort：无新证据即不解禁（§3.1 无停止证明不释放） */
    }
  }

  /** §3.1 非 cancel 终态解禁（COMPLETED/FAILED；GetTask 确认同路）——
   *  CANCELED 终态不走此路（解禁只认 engineAbort=confirmed）。 */
  private releaseBanOnNonCancelTerminal(contextId: string): void {
    this.sendBan.releaseOnTerminal(this.config.a2aUrls.rpcUrl, this.banContext(contextId));
  }

  // ─── B11 恢复面（自动恢复在 request() catch 内联；本节为公共锚点） ────

  /**
   * 未终结任务判据（review R2-3 第二补丁；spec §3.1 销毁时点④的对偶面）：
   * 有恢复锚、无已落终局、无在途请求——即"任务结果未确认、恢复入口在场"
   * （manualRecover 可开轮前提的**只读镜像**，不触发轮）。宿主回滚换轨时据此
   * 决定：true → 旧任务处理器保留（仅让出发送位），false → 可安全断连退役。
   */
  get hasUnterminatedRecovery(): boolean {
    return this.run.recoveryAnchor !== null && !this.run.runSettled && !this.isRequesting;
  }

  /**
   * 未终结任务的取消入口（review R2-3 第四轮：宿主 parked 位驱动）——旧任务
   * 的请求已退出（sentinel：无在途请求），abort() 的 in-flight 门触不到它。
   * 与 abort 的 anchored 分流同面：wire CancelTask + 乐观挂禁 + 控制面核销
   *（§4.3 四态机全生效）。generation 栅栏自增废止在飞中的手工恢复轮
   *（晚到帧一律拒）。§3.1 语义：取消 = 用户明确放弃——宿主随即释放引用。
   */
  cancelUnterminatedTask(): void {
    const anchor = this.run.recoveryAnchor;
    if (!anchor || this.run.runSettled || this.isRequesting) return;
    this.generation += 1;
    this.fireWireCancel(anchor.taskRunId, anchor.sessionKey);
  }

  /**
   * 手工恢复入口（B13/UI 接线；B11 只立 API 不进 UI）。
   * 开一轮**只读**恢复（不做 execute 重发）：fresh per-round 预算（spec：手工轮
   * 自成预算），事件走同一 translate→parser 管线与代际栅栏。
   * null = 无事可恢复（无 taskId / 已终局 / 有在途请求）或恢复中被让位
   * （新请求/abort/disconnect——UI 播报归让位方，本层静默废止）。
   */
  async manualRecover(): Promise<RecoveryOutcome | null> {
    const anchor = this.run.recoveryAnchor;
    if (!anchor || this.run.runSettled || this.isRequesting) return null;
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
      this.run.buildRecoveryEngine(ctrl), this.config.recovery);
    const recovered = await runner.run(
      anchor.taskRunId, this.run.appliedSeq,
      (ev) => {
        if (generation !== this.generation) return;   // 迟帧栅栏：手工轮同门
        this.run.consumeWireEvent(ev, bag);
      },
      { isCancelled: () => generation !== this.generation },
    );
    if (recovered.outcome === 'cancelled') return null;
    if (recovered.outcome === 'resumed-incomplete') {
      this.run.applyRecoveredIncomplete(bag, recovered);
      this.onError?.(new Error(RECOVERY_INCOMPLETE_MESSAGE));
      return recovered;
    }
    if (recovered.outcome === 'unknown') {
      this.onError?.(new Error(OUTCOME_UNKNOWN_MESSAGE));
    } else if (recovered.outcome === 'budget-exhausted') {
      this.onError?.(new Error(RECOVERY_STOPPED_MESSAGE));
    } else if (recovered.outcome === 'fatal') {
      // F4：手工轮同路——硬失败播报宿主（锁检测）+ 预算耗尽等价信号语
      this.run.notifyFatalRecovery(recovered.failure);
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


