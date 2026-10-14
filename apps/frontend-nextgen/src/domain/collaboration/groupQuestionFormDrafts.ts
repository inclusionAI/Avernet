import type { QuestionDraft, QuestionDrafts } from './groupQuestions';

/** 未作答、未跳过的显式空草稿。 */
export const DEFAULT_DRAFT: QuestionDraft = { values: [], other: '', skipped: false };

/** 末题跳过自动提交时，本帧 props 尚未携带最新草稿 → 本地覆盖后再构造 answers。 */
export function withDraftOverride(
  drafts: QuestionDrafts,
  questionId: string,
  draft: QuestionDraft,
): QuestionDrafts {
  const next: QuestionDrafts = Object.create(null);
  for (const [existingId, existing] of Object.entries(drafts)) next[existingId] = existing;
  next[questionId] = draft;
  return next;
}
