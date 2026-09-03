import type { BotTemplateField } from '@/services/botWorkshop/agentCodingTemplateService';

export function isTemplateFieldRequired(field: BotTemplateField): boolean {
  const required = (field as { required?: unknown }).required;
  return required === true || required === 1 || String(required ?? '').toLowerCase() === 'true';
}

export function normalizeFieldKey(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[\s-]/g, '_');
}
