import type { SessionMessageData } from '@/services/backendApi/collaboration/sessionController';
import type { Block, ChatMessage, TextBlock, ToolExecutionBlock, ToolStep } from '@tc-chat/core';
import { imageAttachmentsToBlocks } from './messageBlockBuilder';
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
  tool_args?: unknown;
  bcs_pending?: unknown;
  pending_kind?: unknown;
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
    status:
      (dto.metadata as MessageMetadata | undefined)?.bcs_pending === true
        ? 'running'
        : isToolError(meta)
        ? 'error'
        : 'success',
    input:
      meta.arguments === undefined && (dto.metadata as MessageMetadata | undefined)?.tool_args === undefined
        ? undefined
        : JSON.stringify(meta.arguments ?? (dto.metadata as MessageMetadata | undefined)?.tool_args),
    output: extractMessageContent(meta.result ?? dto.content) || undefined,
  };
}

/** 判定 DTO 是否为 tool_result 形态（带 tool_call_id / tool_name / is_error 任意一个）。 */
function isToolResult(item: GroupHistoryDto): boolean {
  const meta = item.metadata as ToolResultMeta | undefined;
  return Boolean(meta?.tool_call_id || meta?.tool_name || meta?.is_error);
}

function isTerminalToolStatus(status: ToolStep['status']): boolean {
  return status === 'success' || status === 'error';
}

/** 按 tool_call_id 更新已有步骤；新步骤只并入尾部连续的 tool block。 */
function upsertToolStep(blocks: Block[], incoming: ToolStep): Block[] {
  for (let blockIndex = 0; blockIndex < blocks.length; blockIndex += 1) {
    const block = blocks[blockIndex];
    if (block.type !== 'tool_execution') continue;
    const toolBlock = block as ToolExecutionBlock;
    const stepIndex = toolBlock.steps.findIndex((step) => step.id === incoming.id);
    if (stepIndex < 0) continue;

    const existing = toolBlock.steps[stepIndex];
    const preserveTerminal = isTerminalToolStatus(existing.status) && !isTerminalToolStatus(incoming.status);
    const merged: ToolStep = preserveTerminal
      ? {
          ...existing,
          tool: incoming.tool || existing.tool,
          title: incoming.title || existing.title,
          input: incoming.input ?? existing.input,
        }
      : {
          ...existing,
          ...incoming,
          input: incoming.input ?? existing.input,
          output: incoming.output ?? existing.output,
        };
    const steps = toolBlock.steps.map((step, index) => (index === stepIndex ? merged : step));
    return blocks.map((item, index) => (index === blockIndex ? { ...toolBlock, steps } : item));
  }

  const lastIndex = blocks.length - 1;
  const lastBlock = blocks[lastIndex];
  if (lastBlock?.type === 'tool_execution') {
    return blocks.map((item, index) =>
      index === lastIndex
        ? { ...(item as ToolExecutionBlock), steps: [...(item as ToolExecutionBlock).steps, incoming] }
        : item,
    );
  }
  return [...blocks, { type: 'tool_execution', steps: [incoming] } as ToolExecutionBlock];
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
  const assistantByRun = new Map<string, ChatMessage>();
  for (const raw of dtos) {
    const dto = raw as GroupHistoryDto;
    const role = resolveRole(dto);
    if (!role) continue; // SDK 不识别 → 丢弃，避免渲染器崩

    const id = String(dto.id);
    const content = extractMessageContent(dto.content);
    const isPending = dto.metadata?.bcs_pending === true;
    const status: ChatMessage['status'] = isPending ? 'streaming' : 'history';
    const createdAt = normalizeTimestamp(dto.timestamp) ?? 0;
    const roundId = getRoundId(dto);
    const runKey = role === 'assistant' && roundId ? `${roundId}\u0000${String(dto.sender ?? '')}` : null;
    const prev = runKey ? assistantByRun.get(runKey) : undefined;

    // 聚合：同一 bot 的同 run_id 消息合并；允许不同 run 的帧交错
    if (prev) {
      const newStep = isToolResult(dto) ? toolResultToToolStep(dto) : null;
      let blocks: Block[] = [...(prev.blocks ?? [])];
      blocks.push(
        ...imageAttachmentsToBlocks(dto.attachments, {
          resolveAttachmentUrl: (attachment) => {
            const attachmentId = attachment.attachment_id ?? attachment.attachmentId;
            return sessionId && typeof attachmentId === 'string' && attachmentId
              ? sessionFileService.buildContentUrl(sessionId, attachmentId)
              : undefined;
          },
        }),
      );
      if (newStep) {
        blocks = upsertToolStep(blocks, newStep);
      } else if (content) {
        blocks.push({ type: 'text', content } as TextBlock);
      }
      prev.blocks = blocks;
      prev.content = [...(prev.content ? [prev.content] : []), content].filter(Boolean).join('\n');
      if (isPending) {
        prev.status = 'streaming';
        prev.extra = { ...prev.extra, bcsPending: true, metadata: dto.metadata };
      }
      continue;
    }

    const isTool = isToolResult(dto);
    const step = isTool ? toolResultToToolStep(dto) : null;
    const blocks: Block[] = [];
    // 图片附件置顶（图片在上、文本在下），与 open-claw「我的协作」回显一致
    const imageBlocks = imageAttachmentsToBlocks(dto.attachments, {
      resolveAttachmentUrl: (attachment) => {
        const attachmentId = attachment.attachment_id ?? attachment.attachmentId;
        return sessionId && typeof attachmentId === 'string' && attachmentId
          ? sessionFileService.buildContentUrl(sessionId, attachmentId)
          : undefined;
      },
    });
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
    if (roundId) extra.runId = roundId;
    if (role === 'assistant' && dto.sender !== undefined) extra.botUuid = dto.sender;
    if (isPending) {
      extra.bcsPending = true;
      extra.metadata = dto.metadata;
    }

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
    if (runKey) assistantByRun.set(runKey, message);
  }
  return result;
}
