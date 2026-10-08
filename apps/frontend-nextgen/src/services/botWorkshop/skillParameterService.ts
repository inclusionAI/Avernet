import { skillParameterController } from '@/services/backendApi/bots/skillParameterController';
import { load } from 'js-yaml';
export interface SkillParameterField {
  name: string;
  label?: string;
  description?: string;
  type?: string;
  required?: boolean;
  default?: unknown;
  options?: Array<{ value: string | number; label?: string }>;
}
export function parseSkillParameterSchema(content: string): SkillParameterField[] {
  const front = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(content);
  if (!front) return [];
  const meta = load(front[1]) as Record<string, unknown> | null;
  const fields = meta?.parameters ?? meta?.config ?? [];
  if (!Array.isArray(fields) || fields.some((field) => !field || typeof field.name !== 'string'))
    throw new Error('Skill 参数定义格式无效');
  return fields;
}
export function validateSkillParameters(schema: SkillParameterField[], values: Record<string, unknown>) {
  for (const field of schema) {
    const value = values[field.name];
    if (field.required && (value === undefined || value === null || value === ''))
      throw new Error(`${field.label || field.name} 是必填项`);
    if (
      field.type === 'number' &&
      value !== '' &&
      value !== undefined &&
      (typeof value !== 'number' || !Number.isFinite(value))
    )
      throw new Error(`${field.label || field.name} 必须是有效数字`);
  }
}
export const skillParameterService = {
  async get(botId: string, skillId: string, ownerId?: string) {
    const response = await skillParameterController.get(botId, skillId, ownerId);
    if (!response.data) throw new Error('未返回 Skill 参数');
    return response.data.parameters;
  },
  async save(botId: string, skillId: string, parameters: Record<string, unknown>, ownerId?: string) {
    const response = await skillParameterController.save(botId, skillId, parameters, ownerId);
    if (!response.data) throw new Error('未返回保存结果');
    return response.data.parameters;
  },
};
