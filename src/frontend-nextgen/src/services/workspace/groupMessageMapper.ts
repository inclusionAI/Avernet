import type {
  SessionMessageAttachment,
  SessionMessageData,
} from '@/services/backendApi/collaboration/sessionController';
import type { Block, ChatMessage, ImageBlock, TextBlock, ToolExecutionBlock, ToolStep } from '@tc-chat/core';
import { extractMessageContent, isToolError, normalizeTimestamp, type ToolResultMeta } from './messageMapperHelpers';
import { sessionFileService } from './sessionFileService';

/**
 * 后端历史消息 DTO（GET /openapi/v1/collaboration/sessions/{sid}/messages）字段：
 *  - timestamp：毫秒时间戳
 *  - sender：发送者标识（bot_id / user_id）
 *  - message_type：发送者类型 bot / human / system
 *  - role：消息角色 assistant / user / system（与 SDK ChatMessage.role 对齐）
 *  - bot_name：Bot 名称（仅 message_type=bot，用于展示头像与名称）
 *  - run_id：同一运行轮次的 assistant 消息共享此 ID（用于聚合同轮多段文本）
 *
 * 后端在 openapi 严格类型之外可能携带 metadata 字段（tool_name / tool_call_id / arguments /
 * result / success / status / error / is_error 等 tool_result 元信息），Mapper 内部宽松读取。
 */
interface MessageMetadata {
  tool_name?: unknown;
  tool_call_id?: unknown;
  arguments?: unknown;
  result?: unknown;
  success?: unknown;
  status?: unknown;
  error?: unknown;
  is_error?: unknown;
}

export type GroupHistoryDto = SessionMessageData & { metadata?: MessageMetadata & ToolResultMeta };

/** 读取轮次 ID（DTO 的 run_id / metadata 或已映射消息 extra 的 conversationRoundId）。 */
function getRoundId(x: { run_id?: string; metadata?: unknown; extra?: Record<string, unknown> }): string | null {
  if (typeof x?.run_id === 'string' && x.run_id.length > 0) return x.run_id;
  const meta = (x?.metadata as { history_meta?: { conversationRoundId?: string } } | undefined)?.history_meta
    ?.conversationRoundId;
  if (typeof meta === 'string' && meta.length > 0) return meta;
  const fromExtra = x?.extra?.conversationRoundId;
  if (typeof fromExtra === 'string' && fromExtra.length > 0) return fromExtra;
  return null;
}

/** 将带 tool 元信息的 DTO 转换为 SDK ToolStep；不带 tool_name / tool_call_id 返回 null。 */
export function toolResultToToolStep(dto: GroupHistoryDto): ToolStep | null {
  const meta: ToolResultMeta = { ...(dto.metadata as ToolResultMeta | undefined) };
  if (!meta.tool_name && !meta.tool_call_id) return null;
  const id = String(meta.tool_call_id ?? meta.tool_name ?? `step-${dto.id}`);
  const tool = String(meta.tool_name ?? 'tool');
  return {
    id,
    tool,
    title: tool,
    status: isToolError(meta) ? 'error' : 'success',
    input: meta.arguments === undefined ? undefined : JSON.stringify(meta.arguments),
    output: extractMessageContent(meta.result ?? dto.content) || undefined,
  };
}

/** 判定 DTO 是否为 tool_result 形态（带 tool_call_id / tool_name / is_error 任意一个）。 */
function isToolResult(item: GroupHistoryDto): boolean {
  const meta = item.metadata as ToolResultMeta | undefined;
  return Boolean(meta?.tool_call_id || meta?.tool_name || meta?.is_error);
}

/** 图片失效/已删（无 url）时展示的占位图（SVG data URL）。 */
const IMAGE_UNAVAILABLE_PLACEHOLDER =
  'data:image/svg+xml;charset=utf8,' +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='240' height='160'><rect width='240' height='160' fill='#f1f5f9'/><g fill='none' stroke='#cbd5e1' stroke-width='2'><rect x='90' y='45' width='60' height='50' rx='4'/><circle cx='106' cy='62' r='6' fill='#cbd5e1'/><path d='M90 95 L112 73 L132 90 L150 65 L150 95Z' fill='#cbd5e1'/></g><text x='120' y='125' text-anchor='middle' font-family='sans-serif' font-size='14' fill='#94a3b8'>图片不可用</text></svg>`,
  );

/**
 * 把 BCS 图片附件转换为 SDK ImageBlock[]（对齐 open-claw「我的协作」展示方式）。
 * 有 url 直接以 share_url 作 <img src>；无 url（图片已删/失效）展示占位图。
 */
function attachmentsToBlocks(attachments?: SessionMessageAttachment[], sessionId?: string): ImageBlock[] {
  if (!attachments || attachments.length === 0) return [];
  return attachments
    .filter((att) => att.type === 'image')
    .map((att) => ({
      type: 'image' as const,
      data:
        (sessionId && att.attachment_id ? sessionFileService.buildContentUrl(sessionId, att.attachment_id) : att.url) ||
        IMAGE_UNAVAILABLE_PLACEHOLDER,
      name: att.file_name ?? 'image',
      mimeType: att.mime_type ?? 'image/png',
    }));
}

/** 优先用 role 字段（与 SDK 对齐），缺失时按 message_type 兜底映射。 */
function resolveRole(dto: GroupHistoryDto): ChatMessage['role'] | null {
  if (dto.role === 'assistant' || dto.role === 'user' || dto.role === 'system') return dto.role;
  const t = dto.message_type;
  return t === 'bot' ? 'assistant' : t === 'human' ? 'user' : t === 'system' ? 'system' : null;
}

/**
 * 将后端 SessionMessageData[] 映射为 SDK ChatMessage[]：
 *  - message_type/role → SDK role；未知类型 → 丢弃（不抛错）
 *  - 同 run_id 相邻 assistant（含 tool_result）聚合为一条 ChatMessage：
 *      文本块按顺序用 \n 拼接到 content；tool 步骤追加到 tool_execution 块；同 tool_call_id 不重复入列
 *  - tool_result 的文本不混入 content
 *  - 通过 extra 透传 senderId / botName / conversationRoundId 供组件展示头像与名称
 */
export function mapGroupHistoryMessages(dtos: GroupHistoryDto[], sessionId?: string): ChatMessage[] {
  const result: ChatMessage[] = [];
  for (const raw of dtos) {
    const dto = raw as GroupHistoryDto;
    const role = resolveRole(dto);
    if (!role) continue; // SDK 不识别 → 丢弃，避免渲染器崩

    const id = String(dto.id);
    const content = extractMessageContent(dto.content);
    const status: ChatMessage['status'] = 'history';
    const createdAt = normalizeTimestamp(dto.timestamp) ?? 0;
    const roundId = getRoundId(dto);
    const prev = result[result.length - 1];

    // 聚合：相邻 assistant + 同 run_id 的消息合并
    if (prev && prev.role === 'assistant' && role === 'assistant' && roundId && getRoundId(prev) === roundId) {
      const newStep = isToolResult(dto) ? toolResultToToolStep(dto) : null;
      const blocks: Block[] = [...(prev.blocks ?? [])];
      if (newStep) {
        const exists = blocks.some(
          (b) => b.type === 'tool_execution' && (b as ToolExecutionBlock).steps.some((s) => s.id === newStep.id),
        );
        if (!exists) {
          const existingToolBlock = blocks.find((b) => b.type === 'tool_execution') as ToolExecutionBlock | undefined;
          if (existingToolBlock) {
            existingToolBlock.steps = [...existingToolBlock.steps, newStep];
          } else {
            blocks.push({ type: 'tool_execution', steps: [newStep] } as ToolExecutionBlock);
          }
        }
      } else if (content) {
        blocks.push({ type: 'text', content } as TextBlock);
      }
      prev.blocks = blocks;
      prev.content = [...(prev.content ? [prev.content] : []), content].filter(Boolean).join('\n');
      continue;
    }

    const isTool = isToolResult(dto);
    const step = isTool ? toolResultToToolStep(dto) : null;
    const blocks: Block[] = [];
    // 图片附件置顶（图片在上、文本在下），与 open-claw「我的协作」回显一致
    const imageBlocks = attachmentsToBlocks(dto.attachments, sessionId);
    blocks.push(...imageBlocks);
    if (content && !isTool) {
      blocks.push({ type: 'text', content } as TextBlock);
    }
    if (step) {
      blocks.push({ type: 'tool_execution', steps: [step] } as ToolExecutionBlock);
    }

    const extra: Record<string, unknown> = {};
    if (dto.sender !== undefined) extra.senderId = dto.sender;
    if (typeof dto.bot_name === 'string' && dto.bot_name) extra.botName = dto.bot_name;
    if (roundId) extra.conversationRoundId = roundId;

    const message: ChatMessage = {
      id,
      role,
      content: role === 'assistant' && isTool ? '' : content,
      status,
      createdAt,
      blocks,
      ...(Object.keys(extra).length > 0 ? { extra } : {}),
    } as ChatMessage;

    result.push(message);
  }
  return result;
}
