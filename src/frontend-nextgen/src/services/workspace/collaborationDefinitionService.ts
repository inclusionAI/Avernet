import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';
import {
  validateDefinition as validateApi,
  type DefinitionValidationData,
  type ParticipantSlot,
  type ValidationDiagnostic,
} from '@/services/backendApi/collaboration/collaborationDefinitionController';
import { load as parseYaml } from 'js-yaml';
import type { DomainError, DomainResult } from './identityService';

/** 校验成功时返回的领域模型。 */
export interface DefinitionValidation {
  valid: boolean;
  summary: DefinitionValidationData['summary'];
  participants: ParticipantSlot[];
  errors: ValidationDiagnostic[];
  graph?: CollaborationDefinitionGraphPreview;
}

export interface ParticipantDefinition {
  key: string;
  displayName?: string;
  description?: string;
  required: boolean;
}

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

/** 将后端 ParticipantSlot 映射为前端 ParticipantDefinition（过滤空 binding）。 */
export function buildParticipantDefinitions(slots: ParticipantSlot[] = []): ParticipantDefinition[] {
  return slots.flatMap((slot) => {
    const binding = slot.binding.trim();
    if (!binding) return [];
    return [
      {
        key: binding,
        displayName: slot.display_name?.trim() || undefined,
        description: slot.description?.trim() || undefined,
        required: slot.required,
      },
    ];
  });
}

/** 格式化校验错误为可展示字符串。 */
export function formatValidationErrors(errors: ValidationDiagnostic[] | undefined): string {
  if (!errors?.length) return 'YAML 校验未通过';
  return errors
    .map(({ path, message }) => {
      const p = path.trim();
      return p && p !== '$' ? `${p}: ${message}` : message;
    })
    .join('；');
}

function pickString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function parseRequiredValue(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  const normalized = String(value ?? '')
    .trim()
    .toLowerCase();
  return (
    normalized !== 'false' && normalized !== 'no' && normalized !== 'off' && normalized !== '0' && normalized !== ''
  );
}

/** 把单个 participant 原始槽位对象映射为 ParticipantDefinition。 */
function toParticipantDefinition(key: string, slot: unknown): ParticipantDefinition | null {
  if (!key) return null;
  const s = slot && typeof slot === 'object' && !Array.isArray(slot) ? (slot as Record<string, unknown>) : {};
  return {
    key,
    displayName: pickString(s.name) ?? pickString(s.display_name),
    description: pickString(s.description),
    required: s.required === undefined ? true : parseRequiredValue(s.required),
  };
}

/**
 * 前端本地解析协作定义 YAML 的 participants（方案 A，对齐 ocb 纯前端解析）。
 * 不依赖后端 validate 接口契约；解析失败抛 Error，由 useYamlValidation 捕获转 validationError。
 * 兼容两种形态：
 *  - 对象形式：participants:
  speaker:
    required: true
    name: ...
 *  - 数组形式：participants:
  - alpha | { key/name/id, required? }
 */
export function parseCollaborationParticipants(yaml: string): ParticipantDefinition[] {
  let doc: unknown;
  try {
    doc = parseYaml(yaml);
  } catch (err) {
    throw new Error(err instanceof Error ? `YAML 解析失败：${err.message}` : 'YAML 解析失败');
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    throw new Error('YAML 顶层需为对象，且包含 participants 字段');
  }
  const participants = (doc as Record<string, unknown>).participants;
  if (participants === null || participants === undefined) {
    throw new Error('YAML 中未找到 participants 字段');
  }

  const defs: ParticipantDefinition[] = [];
  if (!Array.isArray(participants) && typeof participants === 'object') {
    for (const [key, slot] of Object.entries(participants as Record<string, unknown>)) {
      const def = toParticipantDefinition(key, slot);
      if (def) defs.push(def);
    }
  } else if (Array.isArray(participants)) {
    for (const item of participants) {
      if (typeof item === 'string') {
        const key = item.trim();
        if (key) defs.push({ key, required: true });
        continue;
      }
      if (item && typeof item === 'object') {
        const obj = item as Record<string, unknown>;
        const key = pickString(obj.key) ?? pickString(obj.name) ?? pickString(obj.id);
        if (!key) continue;
        defs.push({
          key,
          displayName: pickString(obj.display_name) ?? pickString(obj.name),
          description: pickString(obj.description),
          required: obj.required === undefined ? true : parseRequiredValue(obj.required),
        });
      }
    }
  } else {
    throw new Error('participants 请使用对象或数组形式定义');
  }

  if (defs.length === 0) throw new Error('participants 至少需要定义一个角色');
  const seen = new Set<string>();
  for (const d of defs) {
    if (seen.has(d.key)) throw new Error(`participants 中存在重复 key：${d.key}`);
    seen.add(d.key);
  }
  return defs;
}

/**
 * 自定义协作定义数据层：校验 YAML 并解包 envelope.data。
 */
export const collaborationDefinitionService = {
  async validate(definitionYaml: string): Promise<DomainResult<DefinitionValidation>> {
    try {
      const resp = await validateApi({ definition_yaml: definitionYaml });
      const data = resp.data;
      if (!data) {
        return { ok: false, error: toDomainError('VALIDATE_FAILED', 'YAML 校验请求失败') };
      }
      return {
        ok: true,
        data: {
          valid: data.valid,
          summary: data.summary,
          participants: data.participants ?? [],
          errors: data.errors ?? [],
          graph: data.graph,
        },
      };
    } catch {
      return { ok: false, error: toDomainError('VALIDATE_FAILED', 'YAML 校验请求失败，请稍后重试。') };
    }
  },
};
