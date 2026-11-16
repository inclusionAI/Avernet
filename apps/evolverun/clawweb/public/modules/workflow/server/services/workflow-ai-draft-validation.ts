import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { parseDocument } from 'yaml';
import { validateSpec } from '../validators/workflow-validator.js';
import { WORKFLOW_AI_DRAFT_MAX_YAML_BYTES, WORKFLOW_AI_DRAFT_MAX_PROMPT_CHARS, WorkflowAiDraftError,
  type WorkflowAiDraftRequest } from '../contracts/workflow-ai-draft.js';

export const aiDraftValidText = (text: string) => Buffer.from(text, 'utf8').toString('utf8') === text;
export const aiDraftSha256 = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex');
export const aiDraftUuid = (value: unknown): value is string => typeof value === 'string'
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
export function assertAiDraftWorkflowId(value: string): void {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(value)
    || value.includes('..') || value.endsWith('.') || value.endsWith('.lock') || /^__.+__$/.test(value)) throw new WorkflowAiDraftError('INVALID_INPUT');
}
// Bound JSON expansion before schema traversal, charging every alias
// occurrence rather than memoizing shared values. Bound before schema traversal.
const MAX_JSON_DEPTH = 48;
const MAX_JSON_VALUES = 20_000;
const MAX_JSON_BYTES = 1024 * 1024;
function boundedJson(value: unknown): unknown {
  let count = 0, bytes = 0;
  const active = new Set<object>();
  const spend = (amount: number) => {
    bytes += amount;
    if (bytes > MAX_JSON_BYTES) throw new WorkflowAiDraftError('PAYLOAD_TOO_LARGE');
  };
  const encoded = (scalar: string | number | boolean | null) => Buffer.byteLength(JSON.stringify(scalar), 'utf8');
  const walk = (child: unknown, depth: number): unknown => {
    if (++count > MAX_JSON_VALUES || depth > MAX_JSON_DEPTH) throw new WorkflowAiDraftError('PAYLOAD_TOO_LARGE');
    if (child instanceof Map || Array.isArray(child)) {
      if (active.has(child)) throw new WorkflowAiDraftError('INVALID_INPUT');
      active.add(child);
      const size = child instanceof Map ? child.size : child.length;
      spend(2 + Math.max(0, size - 1));
      let result: unknown;
      if (child instanceof Map) {
        const mapping: Record<string, unknown> = Object.create(null);
        for (const [key, member] of child) {
          if (typeof key !== 'string' || !aiDraftValidText(key)) throw new WorkflowAiDraftError('INVALID_INPUT');
          spend(encoded(key) + 1);
          mapping[key] = walk(member, depth + 1);
        }
        result = mapping;
      } else result = child.map(member => walk(member, depth + 1));
      active.delete(child);
      return result;
    }
    if (child !== null && typeof child !== 'string' && typeof child !== 'boolean' && typeof child !== 'number') throw new WorkflowAiDraftError('INVALID_INPUT');
    if ((typeof child === 'number' && !Number.isFinite(child)) || (typeof child === 'string' && !aiDraftValidText(child))) throw new WorkflowAiDraftError('INVALID_INPUT');
    spend(encoded(child as string | number | boolean | null));
    return child;
  };
  return walk(value, 0);
}
/** Validate the exact overlay, never a normalized/reserialized replacement. */
export function aiDraftWorkflow(yaml: string, workflowId: string): Record<string, unknown> {
  if (typeof yaml !== 'string' || !aiDraftValidText(yaml) || yaml.includes('\0')) throw new WorkflowAiDraftError('INVALID_INPUT');
  if (Buffer.byteLength(yaml, 'utf8') > WORKFLOW_AI_DRAFT_MAX_YAML_BYTES) throw new WorkflowAiDraftError('PAYLOAD_TOO_LARGE');
  try {
    const document = parseDocument(yaml, { uniqueKeys: true, prettyErrors: false });
    if (document.errors.length || document.warnings.length) throw new WorkflowAiDraftError('INVALID_INPUT');
    const raw = boundedJson(document.toJS({ mapAsMap: true, maxAliasCount: 100 }));
    if (!raw || typeof raw !== 'object' || Array.isArray(raw) || (raw as { id?: unknown }).id !== workflowId
      || !validateSpec(raw).valid) throw new Error('invalid');
    const version = (raw as Record<string, unknown>).version;
    if (!(typeof version === 'string' && version.trim()) && !(typeof version === 'number' && Number.isFinite(version) && version > 0)) throw new WorkflowAiDraftError('INVALID_INPUT');
    return raw as Record<string, unknown>;
  } catch (error) {
    if (error instanceof WorkflowAiDraftError) throw error;
    throw new WorkflowAiDraftError('INVALID_INPUT');
  }
}
export function validateAiDraftRequest(workflowId: string, value: WorkflowAiDraftRequest): void {
  assertAiDraftWorkflowId(workflowId);
  if (!value || !aiDraftUuid(value.requestId) || !['create', 'modify'].includes(value.mode)
    || typeof value.prompt !== 'string' || !aiDraftValidText(value.prompt) || !value.prompt.trim()
    || value.prompt.includes('\0')) throw new WorkflowAiDraftError('INVALID_INPUT');
  if ([...value.prompt].length > WORKFLOW_AI_DRAFT_MAX_PROMPT_CHARS) throw new WorkflowAiDraftError('PAYLOAD_TOO_LARGE');
  aiDraftWorkflow(value.currentYaml, workflowId);
}
export function validateAiDraftOutput(yaml: string, workingYaml: string, workflowId: string): void {
  const result = aiDraftWorkflow(yaml, workflowId);
  const overlay = aiDraftWorkflow(workingYaml, workflowId);
  if (!isDeepStrictEqual(result.version, overlay.version)) throw new WorkflowAiDraftError('INVALID_INPUT');
}
