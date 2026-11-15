export interface CollaborationDraftReference {
  schemaVersion: 2;
  draftId: string;
  revision: number;
}

export interface CollaborationDraft {
  reference: CollaborationDraftReference;
  definitionYaml: string;
  title?: string;
}

export interface OpenCreateGroupOptions {
  draft?: CollaborationDraftReference;
}

export function decodeOpenCreateGroupOptions(input: unknown = {}): OpenCreateGroupOptions {
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).some((key) => key !== 'draft')
  ) {
    throw new Error('发起协作参数无效');
  }
  const draft = (input as OpenCreateGroupOptions).draft;
  if (draft === undefined) return {};
  if (
    !draft ||
    typeof draft !== 'object' ||
    Array.isArray(draft) ||
    draft.schemaVersion !== 2 ||
    typeof draft.draftId !== 'string' ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(draft.draftId) ||
    !Number.isSafeInteger(draft.revision) ||
    draft.revision < 1
  ) {
    throw new Error('协作版本引用无效');
  }
  return { draft: { schemaVersion: 2, draftId: draft.draftId, revision: draft.revision } };
}
