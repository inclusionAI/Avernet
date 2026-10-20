/**
 * B10 — 真 SDK OpenClawParser 运行时加载器（A2AProvider 的解析 oracle）。
 * ============================================================================
 *
 * 为什么不直接 `import { OpenClawParser } from '@tc-chat/adapters'`：
 *   1. 顶层正名 import 会把 adapters 全量子模块族拖进命名方 chunk，运行时
 *      才确定要不要 A2A（传输选择探测先走）——动态加载让 A2A 面整个按需；
 *   2. 真正的 app 构建里该包是真实依赖，且必须被 webpack 静态打进浏览器包
 *      ——运行时动态 `import()` 用**字面量子路径**，保证 webpack 能解析成 chunk。
 *
 * 候选序（与 test/adapters/a2a/v3-frame-shapes.test.ts 的 test-scoped 加载器
 * 一致，该测试文件属钉板基建不动；本文件是运行时正式份）：
 *   1) 包名动态 import：真 app 构建（浏览器 chunk）+ jest 全装环境直命中
 *      （@tc-chat/adapters@2.1.0 是本 app 硬依赖；jest 经 babel 转译其 ESM
 *       dist 后 require 直取真 dist）；
 *   2) node 兜底 require（eval('require') 动态取，见下）：
 *      A2A_OPENCLAW_PARSER_PATH 环境变量显式覆盖 → 指到任一真 dist 文件。
 *
 * eval('require')：node 兜底路径**绝不能**被 webpack 静态分析（宿主绝对路径 /
 * NODE_MODULES 环境探测都会被打进浏览器包），经 eval 动态取得到后 webpack 拿到
 * 的是运行时未定义的 `require`（bundle 作用域里是 __webpack_require__）→
 * ReferenceError → 走 catch 返回 undefined，浏览器构建恒走候选 1。
 *
 * Open Core 边界：兜底不内嵌任何宿主机个人绝对路径——env 显式覆盖即唯一
 * 外部位（本 app 的包安装位恒有真 dist，个人路径兜底是死代码且泄宿主信息）。
 */
import type { A2aOpenClawInput } from './A2AProviderInput';

export interface SdkParserConfig {
  enableThinking?: boolean;
  generateId?: () => string;
  xIAMToken?: string;
}

/** @tc-chat/core ParseResult 的结构镜像（运行时依据 B8 钉板契约） */
export interface SdkParseResult {
  blocks: Record<string, any>[];
  content: string;
  done: boolean;
  toolCalls: Record<string, any>[];
  sessionKey?: string;
  isApproval: boolean;
  approvalRequest?: Record<string, any>;
  /** inject 消息独立作用域标识（runId='inject-*'） */
  runId?: string;
}

export interface SdkPendingApproval {
  approvalId: string;
  approvalData: Record<string, any>;
  approvalBlock: Record<string, any>;
  blockedMessages: Record<string, any>[];
}

/** OpenClawParser 公共面（OpenClawParser.d.ts: ProtocolParser 全成员）；
 *  OpenClawParser 的真面比消费面更大（hydrate/markAborted/buildInjectRequest
 *  等 A2AProvider 不消费，结构镜像按消费面收口） */
export interface SdkParser {
  onUpdate?: (result: SdkParseResult) => void;
  onComplete?: (result: SdkParseResult) => void;
  onError?: (error: Error) => void;
  getMessageId(): string;
  getResult(): SdkParseResult;
  reset(): void;
  handleMessage(message: Record<string, any>): boolean;
  buildRequest(params: A2aOpenClawInput): Record<string, any>;
  updateApprovalStatus(approvalId: string, status: 'approved' | 'rejected'): boolean;
  clearPendingApproval(): Record<string, any>[];
  getPendingApproval(): SdkPendingApproval | null;
  setSessionKey(sessionKey: string): void;
}

export type SdkParserCtor = new (config?: SdkParserConfig) => SdkParser;

/** 显式覆盖入口（node/jest 兜底；浏览器因无 require 天然短路） */
const ENV_OVERRIDE_KEY = 'A2A_OPENCLAW_PARSER_PATH';

/**
 * eval('require')：躲开 webpack/打包器静态分析（浏览器 bundle 无 require → catch）。
 * 安全线：eval 的实参是**固定字面量** 'require'，永不拼接任何外部输入，无注入面；
 * 唯一目的就是在 node/jest 里取得 CJS require 引用（打包产物作用域里它不存在）。
 */
function nodeRequire(id: string): unknown {
  try {
    const req = eval('require');               // eslint-disable-line no-eval
    return typeof req === 'function' ? req(id) : undefined;
  } catch {
    return undefined;                           // 浏览器构建：无 require，候选 1 兜住
  }
}

/** 候选 1：包名动态 import——字面量子路径保证 webpack 静态可解析（浏览器 chunk 正路）。 */
function loadFromPackage(): Promise<SdkParserCtor | null> {
  // 下行禁止改参数为变量：webpack 需要字面量才能打包成 chunk。
  // @ts-ignore 解析器差异由运行时 catch 吞掉（未装场景走 node 兜底）。
  return import('@tc-chat/adapters/dist/openclaw/OpenClawParser.js')
    .catch(() => null)                           // 包未装/子路径缺失 → node 兜底
    .then((mod) => ((mod as { OpenClawParser?: SdkParserCtor } | null)
      ?.OpenClawParser) ?? null);
}

/**
 * 真 OpenClawParser 构造器。失败即抛——A2A 没有手搓的兜底 parser
 * （B8 钉板契约：真 parser 就是 oracle）。
 */
export async function loadOpenClawParser(): Promise<SdkParserCtor> {
  const viaRelay = await loadFromPackage();
  if (viaRelay) return viaRelay;
  const envPath = typeof process !== 'undefined'
    ? process.env[ENV_OVERRIDE_KEY] : undefined;
  if (envPath) {
    const mod = nodeRequire(envPath) as
      { OpenClawParser?: SdkParserCtor } | undefined;
    if (mod && mod.OpenClawParser) return mod.OpenClawParser;
  }
  throw new Error(
    'OpenClawParser not found: install @tc-chat/adapters or set ' +
    `${ENV_OVERRIDE_KEY} to dist/openclaw/OpenClawParser.js`,
  );
}