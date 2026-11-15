/** BaaS response decoding only. Never execute Bot output or echo raw diagnostics. */
import { WorkflowAiDraftInvalidResultError, type WorkflowAiDraftPollResult } from '@avernet/workflow/server/contracts/workflow-ai-draft';
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
function text(value: unknown): string {
  if (typeof value === 'string') return value;
  if (!Array.isArray(value)) return '';
  return value.map(block => {
    const item = record(block);
    return item && (!item.type || item.type === 'text') && typeof item.text === 'string' ? item.text : '';
  }).filter(Boolean).join('\n');
}
function reply(data: Record<string, unknown>): string {
  const result = record(data.result);
  if (result && text(result.content)) return text(result.content);
  if (Array.isArray(data.messages)) {
    for (const value of [...data.messages].reverse()) {
      const item = record(value);
      if (item && (item.role === 'assistant' || item.sender === 'bot')) {
        const content = text(item.content) || text(item.text) || text(item.message) || text(item.body);
        if (content) return content;
      }
    }
  }
  return text(data.content) || text(data.text) || text(data.message) || text(data.body);
}
export function decodeWorkflowAiDraftBaasReply(value: unknown): WorkflowAiDraftPollResult {
  const outer = record(value);
  if (!outer) throw new WorkflowAiDraftInvalidResultError('INVALID_BAAS_RESPONSE');
  // ApiResponse.code=0 is success. Error-envelope message is a diagnostic,
  // never assistant output (even if it contains valid fenced YAML).
  if ([outer.code, outer.buserviceErrorCode].some(code => code != null && String(code).trim() !== '' && String(code) !== '0')) {
    return { status: 'failed', error: 'BAAS_PROVIDER_REJECTED' };
  }
  const data = Object.hasOwn(outer, 'data') ? record(outer.data) : outer;
  if (!data) throw new WorkflowAiDraftInvalidResultError('INVALID_BAAS_RESPONSE');
  const status = String(data.status ?? data.message_status ?? '').toLowerCase();
  if (['failed', 'error', 'cancelled', 'canceled', 'stopped', 'aborted', 'time_out'].includes(status)) return { status: 'failed', error: 'BAAS_GENERATION_FAILED' };
  if (['running', 'pending', 'processing', 'queued', 'accepted'].includes(status)) return { status: 'running' };
  const completed = ['completed', 'complete', 'succeeded', 'success', 'done'].includes(status);
  if (status && !completed) return { status: 'running' };
  const response = reply(data);
  if (!response.trim()) {
    if (completed) throw new WorkflowAiDraftInvalidResultError('BAAS_EMPTY_REPLY');
    return { status: 'running' };
  }
  // Exactly one complete YAML block, or raw YAML. Multiple candidates are ambiguous.
  const blocks = [...response.matchAll(/^```(?:yaml|yml)[ \t]*\r?\n([\s\S]*?)^```[ \t]*$/gim)];
  if (blocks.length > 1) throw new WorkflowAiDraftInvalidResultError('BAAS_AMBIGUOUS_YAML');
  const yaml = blocks.length === 1 ? blocks[0][1] : response;
  if (!yaml.trim()) throw new WorkflowAiDraftInvalidResultError('BAAS_EMPTY_YAML');
  // Core performs full schema, graph, identity, version and byte-budget validation.
  return { status: 'completed', result: { yaml, summary: '已通过 AutoHeal Bot 生成 YAML 草稿；请审阅差异后手动应用。' } };
}
