import type { BotMessageDto } from '@/services/backendApi/bots/privateBotSessionController';
import type { ChatMessage, MessageRole, TextBlock, ToolExecutionBlock, ToolStep } from '@tc-chat/core';
import { isToolError, stringifyToolValue } from './messageMapperHelpers';

function toRole(role: BotMessageDto['role']): MessageRole | null {
  if (role === 'user' || role === 'assistant' || role === 'system') return role;
  return null; // tool_use / tool_result 暂不渲染(YAGNI)
}

function parseTimestamp(raw: string): number {
  if (!raw) return NaN;
  return Date.parse(raw);
}

interface ToolMessageMetadata {
  tool_call_id?: unknown;
  toolCallId?: unknown;
  tool_name?: unknown;
  toolName?: unknown;
  arguments?: unknown;
  input?: unknown;
  tool_args?: unknown;
  result?: unknown;
  output?: unknown;
  success?: unknown;
  status?: unknown;
  error?: unknown;
  is_error?: unknown;
}

function getToolMetadata(message: BotMessageDto): ToolMessageMetadata {
  return (message.metadata ?? {}) as ToolMessageMetadata;
}

function getToolStep(message: BotMessageDto, index: number): ToolStep | null {
  const metadata = getToolMetadata(message);
  const callId = metadata.tool_call_id ?? metadata.toolCallId ?? message.message_id ?? `tool-${index}`;
  const callIdText = String(callId || '');
  const toolName = String(metadata.tool_name ?? metadata.toolName ?? 'tool');
  if (!callIdText || (toolName === 'tool' && message.role === 'tool_use' && !metadata.arguments && !metadata.input)) {
    return null;
  }
  const result = metadata.result ?? metadata.output ?? (message.role === 'tool_result' ? message.content : undefined);
  return {
    id: callIdText,
    tool: toolName,
    title: toolName,
    status: message.role === 'tool_result' && isToolError(getToolMetadata(message)) ? 'error' : 'success',
    input: stringifyToolValue(metadata.arguments ?? metadata.input ?? metadata.tool_args),
    output: stringifyToolValue(result ?? metadata.error),
  };
}

/** BotMessageDto[] → ChatMessage[]。入参来自后端「最新页在前、页内正序」,本函数按 gmt_create 升序排列
 *  为「旧→新」并完成 role/content 映射与空值过滤;无时间戳时保持原入参相对顺序(稳定排序)。 */
export function mapBotSessionMessages(items: BotMessageDto[]): ChatMessage[] {
  const indexed = items.map((m, index) => ({ m, index }));
  indexed.sort((a, b) => {
    const ta = parseTimestamp(a.m.gmt_create);
    const tb = parseTimestamp(b.m.gmt_create);
    const aValid = Number.isFinite(ta);
    const bValid = Number.isFinite(tb);
    if (aValid && bValid) return ta - tb;
    if (aValid !== bValid) return aValid ? -1 : 1;
    return a.index - b.index;
  });

  const out: ChatMessage[] = [];
  const toolMessagesByCallId = new Map<string, ChatMessage>();
  indexed.forEach(({ m, index }) => {
    if (m.role === 'tool_use' || m.role === 'tool_result') {
      const step = getToolStep(m, index);
      if (step) {
        const existingMessage = toolMessagesByCallId.get(step.id);
        if (!existingMessage) {
          const createdAt = m.gmt_create ? Date.parse(m.gmt_create) : undefined;
          const chatMessage: ChatMessage = {
            id: m.message_id || `bot-history-${index}-${createdAt ?? 0}`,
            role: 'assistant',
            content: '',
            status: 'history',
            createdAt: Number.isFinite(createdAt) ? createdAt : undefined,
            blocks: [{ type: 'tool_execution', steps: [step] } as ToolExecutionBlock],
          };
          out.push(chatMessage);
          toolMessagesByCallId.set(step.id, chatMessage);
        } else {
          const toolBlock = existingMessage.blocks?.[0] as ToolExecutionBlock | undefined;
          const existingStep = toolBlock?.steps[0];
          if (toolBlock && existingStep) {
            const terminalStatus = existingStep.status === 'success' || existingStep.status === 'error';
            toolBlock.steps[0] = {
              ...existingStep,
              ...step,
              tool: existingStep.tool !== 'tool' ? existingStep.tool : step.tool,
              title: existingStep.title !== 'tool' ? existingStep.title : step.title,
              input: existingStep.input ?? step.input,
              output: existingStep.output ?? step.output,
              status: terminalStatus && step.status === 'success' ? existingStep.status : step.status,
            };
          }
        }
      }
      return;
    }
    const role = toRole(m.role);
    if (!role) return;
    const content = m.content ?? '';
    if (!content) return;
    const createdAt = m.gmt_create ? Date.parse(m.gmt_create) : undefined;
    out.push({
      id: m.message_id || `bot-history-${index}-${createdAt ?? 0}`,
      role,
      content,
      status: 'history',
      createdAt: Number.isFinite(createdAt) ? createdAt : undefined,
      blocks: [{ type: 'text', content }] as TextBlock[],
    });
  });
  return out;
}
