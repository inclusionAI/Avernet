import {
  fetchMyInviteCodeBinding,
  INVITE_CODE_ENDPOINTS,
  postBindInviteCode,
  type InviteCodeBindingDto,
} from '@/services/backendApi/collaboration/inviteCodeController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { isEnvelopeSuccessAnyDialect } from '@/services/backendApi/types';

/**
 * 邀请码准入门禁业务规则层（无 React/DOM/toast 依赖，守分层）：
 * 接口数据（5 位码信封）↔ 领域模型转换、输入轻清洗、后端错误码标准化。
 * 信封解包限定在邀请码域边界（成功信封 {code:20000,...} → 取 data），不跨 support-five-digit-envelope-codes 全局判定。
 *
 * 分层注：本文件位于 `src/services/collaboration/`（领域 Service 层），HTTP 控制器下沉至
 * `src/services/backendApi/collaboration/inviteCodeController`。Hook 经 `@/services/collaboration` 入口消费，
 * 不越层 import `src/services/backendApi/**`（守 `check-boundaries` TC-G002）。
 */

/** 邀请码绑定状态领域模型（剥离信封）。未绑定时 bound_at 缺省。 */
export interface InviteCodeBindingView {
  bound: boolean;
  bound_at?: number;
}

/** 提交绑定结果领域模型。成功恒为 bound:true + bound_at。 */
export interface BindInviteCodeResult {
  bound: boolean;
  bound_at: number;
}

/** 邀请码后端错误码（标准化为领域枚举）。`invite_code_required` 是 gate 反应式识别键（由 inviteGateFailurePolicy 消费，非本层）。 */
export type InviteCodeErrorCode =
  | 'invite_code_unavailable'
  | 'invite_code_already_bound'
  | 'invalid_request'
  | 'unknown';

/** 领域错误：携带标准化错误码，供 Hook 做内联弹窗错误态（不抛到全局 toast——见 httpClient 接缝静默处置）。 */
export class InviteCodeServiceError extends Error {
  readonly code: InviteCodeErrorCode;
  constructor(code: InviteCodeErrorCode, message: string) {
    super(message);
    this.name = 'InviteCodeServiceError';
    this.code = code;
  }
}

/** 输入轻清洗：移除所有空白字符并转大写（后端 normalize_code 兜底，两端一致）。 */
export function normalizeCode(raw: string): string {
  return (raw ?? '').replace(/\s/g, '').toUpperCase();
}

const FRIENDLY_MESSAGES: Record<InviteCodeErrorCode, string> = {
  invite_code_unavailable: '邀请码无效，请检查后重试',
  invite_code_already_bound: '已绑定其他邀请码，请联系管理员',
  invalid_request: '请输入邀请码',
  unknown: '邀请码提交失败，请稍后重试',
};

/** 错误码 → 友好文案（供弹窗内联错误展示）。 */
export function friendlyInviteCodeMessage(code: InviteCodeErrorCode): string {
  return FRIENDLY_MESSAGES[code];
}

/** 从后端信封体读取 error_code（信封 `data.error_code`，5 位码方言）。未知/缺失 → 'unknown'。 */
function readEnvelopeErrorCode(data: unknown): InviteCodeErrorCode {
  const env = data as { data?: { error_code?: unknown } } | undefined;
  const ec = env?.data?.error_code;
  if (ec === 'invite_code_unavailable' || ec === 'invite_code_already_bound' || ec === 'invalid_request') {
    return ec;
  }
  return 'unknown';
}

/**
 * 从抛出的错误（httpClient `BackendRequestError`，`data` 为完整信封）读取后端 error_code。
 * 兼容非 BackendRequestError（网络/未知）→ 'unknown'。
 */
function readErrorCode(error: unknown): InviteCodeErrorCode {
  if (error instanceof BackendRequestError) {
    return readEnvelopeErrorCode(error.data);
  }
  return 'unknown';
}

/** 解包成功信封为绑定视图；HTTP 失败已由 httpClient 抛错（不走此处），此处兜底防 2xx 失败信封。 */
function toBindingView(envelope: unknown): InviteCodeBindingView {
  if (!isEnvelopeSuccessAnyDialect(envelope)) {
    // 网关误包 2xx 但信封失败（罕见）：按错误码标准化上抛。
    throw new InviteCodeServiceError(
      readEnvelopeErrorCode(envelope),
      friendlyInviteCodeMessage(readEnvelopeErrorCode(envelope)),
    );
  }
  const dto = (envelope as { data?: InviteCodeBindingDto }).data;
  return { bound: dto?.bound ?? false, bound_at: dto?.bound_at };
}

/**
 * 查询当前登录 Human 的邀请码绑定状态。成功未绑 → {bound:false}；已绑 → {bound:true,bound_at}。
 * 失败标准化为 `InviteCodeServiceError`（code + 友好文案）；调用方（Hook）决定静默吞或提示。
 */
export async function getMyInviteCodeBinding(): Promise<InviteCodeBindingView> {
  try {
    const envelope = await fetchMyInviteCodeBinding();
    return toBindingView(envelope);
  } catch (error) {
    if (error instanceof InviteCodeServiceError) throw error;
    throw new InviteCodeServiceError(readErrorCode(error), friendlyInviteCodeMessage(readErrorCode(error)));
  }
}

/**
 * 提交邀请码完成绑定。输入经 `normalizeCode` 轻清洗后发送。成功 → {bound:true,bound_at}（含重绑相同码幂等成功）。
 * 失败（`invite_code_unavailable` / `invite_code_already_bound` / `invalid_request`）标准化上抛 `InviteCodeServiceError`。
 */
export async function bindInviteCode(rawCode: string): Promise<BindInviteCodeResult> {
  const code = normalizeCode(rawCode);
  try {
    const envelope = await postBindInviteCode(code);
    const view = toBindingView(envelope);
    return { bound: view.bound, bound_at: view.bound_at ?? 0 };
  } catch (error) {
    if (error instanceof InviteCodeServiceError) throw error;
    throw new InviteCodeServiceError(readErrorCode(error), friendlyInviteCodeMessage(readErrorCode(error)));
  }
}

export { INVITE_CODE_ENDPOINTS };
