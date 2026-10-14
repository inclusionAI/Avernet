/**
 * BCS 群聊 ask_user interaction 帧解析——纯函数，无 I/O。
 *
 * 依据 bcs-provider-2.0-sse-protocol.md §6.2/§7：
 * - 事件帧只认可信 envelope（group_id/bcsRunId/bcsSessionId/bot_uuid 顶层字段），
 *   group/session 与当前群/会话严格相等，不使用字符串包含等宽松匹配。
 * - 仅消费 kind=ask_user；requested 严格校验并按协议缺省值规范化 questions；
 *   accepted/resolved/invalidated 终态不要求 questions。
 * - resolve 响应按顶层 ok/error 解析，终态状态读取 error.details.interactionStatus。
 *
 * 解析失败一律返回 null：无效帧不产生可提交的表单，交回原有 SDK 链路。
 */
import type { GroupQuestion } from '@/domain/collaboration/groupQuestions';

export type GroupQuestionEventPhase = 'requested' | 'accepted' | 'resolved' | 'invalidated';

export type GroupQuestionInteractionStatus = 'pending' | 'accepted' | 'resolved' | 'invalidated';

/** 事件帧解析结果：requested 携带规范化 questions，终态只携带身份。 */
export type ParsedGroupQuestionEvent =
  | {
      phase: 'requested';
      key: string;
      runId: string;
      botId: string;
      interactionId: string;
      questions: GroupQuestion[];
    }
  | {
      phase: 'accepted' | 'resolved' | 'invalidated';
      key: string;
      runId: string;
      botId: string;
      interactionId: string;
    };

/** interaction.resolve 响应解析结果；错误结构取自顶层 error，终态状态取自 error.details.interactionStatus。 */
export type ParsedGroupQuestionResolveResponse =
  | {
      requestId: string;
      ok: true;
      /** 从 payload.interactionStatus 读取。 */
      interactionStatus: GroupQuestionInteractionStatus | null;
      error: null;
    }
  | {
      requestId: string;
      ok: false;
      /** 从 error.details.interactionStatus 读取。 */
      interactionStatus: GroupQuestionInteractionStatus | null;
      /** 解析保证 ok:false 时顶层 error.code/message 存在。 */
      error: { code: string; message: string; retryable: boolean };
    };

/** 当前 service 所属群与会话，用于可信 envelope 的严格相等校验。 */
export interface GroupQuestionEventIdentity {
  groupId: string;
  bcsSessionId: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonBlankString(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== '';
}

function readInteractionStatus(value: unknown): GroupQuestionInteractionStatus | null {
  return value === 'pending' || value === 'accepted' || value === 'resolved' || value === 'invalidated'
    ? value
    : null;
}

/**
 * 校验并规范化 requested 的 questions。
 *
 * 必需字段：questionId/question 非空白且 questionId 唯一；可选字段类型守卫：
 * multiSelect/allowOther 出现时必须是 boolean，header/description 出现时必须是字符串。
 * 缺省语义：multiSelect 省略为 false；allowOther 仅显式 false 才禁止自定义输入。
 * options 可选（缺省即自由文本题），出现时必须是非空数组且每项 value/label 非空白。
 */
function parseQuestions(rawQuestions: unknown): GroupQuestion[] | null {
  if (!Array.isArray(rawQuestions) || rawQuestions.length === 0) return null;
  const questions: GroupQuestion[] = [];
  const seenQuestionIds = new Set<string>();
  for (const item of rawQuestions) {
    if (!isRecord(item)) return null;
    const { questionId, question: questionText, header, multiSelect, allowOther, options } = item;
    if (!isNonBlankString(questionId)) return null;
    if (seenQuestionIds.has(questionId)) return null;
    if (!isNonBlankString(questionText)) return null;
    if (header !== undefined && typeof header !== 'string') return null;
    if (multiSelect !== undefined && typeof multiSelect !== 'boolean') return null;
    if (allowOther !== undefined && typeof allowOther !== 'boolean') return null;

    const parsedOptions: Array<{ value: string; label: string; description?: string }> = [];
    if (options !== undefined) {
      if (!Array.isArray(options) || options.length === 0) return null;
      for (const option of options) {
        if (!isRecord(option)) return null;
        if (!isNonBlankString(option.value) || !isNonBlankString(option.label)) return null;
        if (option.description !== undefined && typeof option.description !== 'string') return null;
        parsedOptions.push(
          option.description !== undefined
            ? { value: option.value, label: option.label, description: option.description }
            : { value: option.value, label: option.label },
        );
      }
    }

    questions.push({
      questionId,
      question: questionText,
      ...(header !== undefined ? { header } : {}),
      multiSelect: multiSelect === true,
      allowOther: allowOther !== false,
      options: parsedOptions,
    });
    seenQuestionIds.add(questionId);
  }
  return questions;
}

/**
 * 解析入站 interaction 事件帧。
 *
 * 仅接受当前群/会话的 ask_user 帧；requested 需携带合法 questions，终态不需要。
 * 交互身份 key 使用 `JSON.stringify([bcsRunId, interactionId])`，避免分隔符碰撞。
 * 返回 null 表示该帧不属于本 service（非 interaction、其他群/会话、非 ask_user 或
 * 数据非法），调用方应将其交回原有 SDK 链路。
 */
export function parseGroupQuestionEvent(
  frame: unknown,
  identity: GroupQuestionEventIdentity,
): ParsedGroupQuestionEvent | null {
  if (!isRecord(frame)) return null;
  if (frame.type !== 'event' || frame.event !== 'interaction') return null;
  if (frame.group_id !== identity.groupId) return null;
  if (frame.bcsSessionId !== identity.bcsSessionId) return null;
  if (!isNonBlankString(frame.bcsRunId)) return null;
  if (!isNonBlankString(frame.bot_uuid)) return null;
  const { payload } = frame;
  if (!isRecord(payload)) return null;
  if (payload.kind !== 'ask_user') return null;
  if (!isNonBlankString(payload.interactionId)) return null;

  const base = {
    key: JSON.stringify([frame.bcsRunId, payload.interactionId]),
    runId: frame.bcsRunId,
    botId: frame.bot_uuid,
    interactionId: payload.interactionId,
  };

  if (payload.phase === 'requested') {
    const questions = parseQuestions(payload.questions);
    if (!questions) return null;
    return { phase: 'requested', ...base, questions };
  }
  if (payload.phase === 'accepted' || payload.phase === 'resolved' || payload.phase === 'invalidated') {
    return { phase: payload.phase, ...base };
  }
  return null;
}

/**
 * 解析 interaction.resolve 响应帧（type=res）。
 *
 * id 由调用方匹配自己的等待后才调用；本函数只做结构校验：
 * ok 必须是 boolean；ok:false 必须携带顶层 error.code/message。
 * 其他 res 帧（SDK 自己的请求响应）一律返回 null，交回原链路处理。
 */
export function parseGroupQuestionResolveResponse(frame: unknown): ParsedGroupQuestionResolveResponse | null {
  if (!isRecord(frame)) return null;
  if (frame.type !== 'res') return null;
  if (typeof frame.id !== 'string' || frame.id === '') return null;
  if (frame.ok !== true && frame.ok !== false) return null;

  if (frame.ok) {
    const payload = isRecord(frame.payload) ? frame.payload : {};
    return {
      requestId: frame.id,
      ok: true,
      interactionStatus: readInteractionStatus(payload.interactionStatus),
      error: null,
    };
  }

  const error = frame.error;
  if (!isRecord(error)) return null;
  if (!isNonBlankString(error.code) || typeof error.message !== 'string') return null;
  const details = isRecord(error.details) ? error.details : {};
  return {
    requestId: frame.id,
    ok: false,
    interactionStatus: readInteractionStatus(details.interactionStatus),
    error: { code: error.code, message: error.message, retryable: error.retryable === true },
  };
}