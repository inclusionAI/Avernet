import type {
  CollaborationDefinitionParticipantSlot,
  CollaborationDefinitionValidationDiagnostic,
} from '@/services/backend-api/BcnController';

export interface CollaborationParticipantDefinition {
  key: string;
  name: string;
  displayName?: string;
  description?: string;
  required: boolean;
  assigned: boolean;
}

export function buildCollaborationParticipantDefinitions(
  slots: CollaborationDefinitionParticipantSlot[] = [],
): CollaborationParticipantDefinition[] {
  return slots.flatMap((slot) => {
    const binding = slot.binding.trim();
    if (!binding) {
      return [];
    }

    const displayName = slot.display_name?.trim();
    const description = slot.description?.trim();
    return [
      {
        key: binding,
        name: binding,
        displayName: displayName || undefined,
        description: description || undefined,
        required: slot.required,
        assigned: slot.assigned,
      },
    ];
  });
}

export function getCollaborationParticipantLabel(
  participant: CollaborationParticipantDefinition,
): string {
  return participant.displayName?.trim() || participant.name;
}

export function formatCollaborationValidationErrors(
  errors: Array<{ path: string; message: string; code?: string; hint?: string }> | undefined,
): string {
  if (!errors?.length) {
    return 'YAML 校验未通过';
  }

  return errors
    .map(({ path, message, code, hint }) => {
      const normalizedPath = path.trim();
      const diagnostic = normalizedPath && normalizedPath !== '$'
        ? `${normalizedPath}: ${message}`
        : message;
      return `${code ? `[${code}] ` : ''}${diagnostic}${hint ? `（${hint}）` : ''}`;
    })
    .join('；');
}

export function canExecuteValidatedCollaboration(
  warnings: CollaborationDefinitionValidationDiagnostic[] = [],
): boolean {
  return !warnings.some(({ code }) => code === 'VALIDATION_ONLY_FEATURE');
}
