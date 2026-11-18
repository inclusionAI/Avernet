import type { EditorSpec } from './editorTypes';

/** Explicit request scope. Never inferred from the active identity store. */
export interface WorkflowAuthoringIdentity { user_id: string }
export type WorkflowAuthoringSpec = EditorSpec;
export interface WorkflowAuthoringIssue { path: string; message: string; severity: 'error' | 'warning' }
export interface WorkflowAuthoringValidation {
  valid: boolean;
  issues: WorkflowAuthoringIssue[];
  errors: WorkflowAuthoringIssue[];
  warnings: WorkflowAuthoringIssue[];
  spec: WorkflowAuthoringSpec | null;
  normalizedSpec: WorkflowAuthoringSpec | null;
}
export interface WorkflowCreationInput {
  workflowId: string;
  title: string;
  packId?: string;
  botId?: string;
  botOwnerId?: string;
  facade?: { command?: string; remark?: string };
}
export interface WorkflowCreateFromSpecInput extends Omit<WorkflowCreationInput, 'title'> {
  title?: string;
  spec: WorkflowAuthoringSpec;
}
export interface WorkflowCreateFromExistingInput extends WorkflowCreationInput { sourceWorkflowId: string }
export interface WorkflowCloneCandidate { workflowId: string; title: string; packId: string | null; updatedAt: number }
export interface WorkflowClonePreview {
  sourceWorkflowId: string; sourcePackId: string; sourceTitle: string; baseCommit: string; scriptCount: number;
}
export interface WorkflowCloneResult extends WorkflowClonePreview {
  targetWorkflowId: string; targetPackId: string; branch: string; commit: string;
}
export type WorkflowAiJobStatus = 'pending' | 'running' | 'dispatch_uncertain' | 'completed' | 'failed' | 'cancelled';
export interface WorkflowAiResult { yaml: string; summary?: string }
export interface WorkflowAiModificationInput { requestId: string; prompt: string; currentYaml: string; mode?: 'create' | 'modify' }
export interface WorkflowAiGenerationInput {
  prompt: string;
  workflowId?: string;
  currentYaml?: string;
  requestId?: string;
  context?: { packId?: string; existingYaml?: string };
  baasSessionId?: string;
}
export interface WorkflowAiDraftJob {
  kind: 'modification';
  draftId: string;
  receipt: string;
  workflowId: string;
  userId: string;
  requestId: string;
  currentYaml: string;
  inputSpecDigest: string;
  status: WorkflowAiJobStatus;
  result?: WorkflowAiResult;
  error?: string;
}
export interface WorkflowAiGenerationJob {
  kind: 'generation';
  generationId: string;
  userId: string;
  status: WorkflowAiJobStatus;
  sessionId?: string;
  messageId?: string;
  result?: WorkflowAiResult;
  error?: string;
  /** Receipt cancellation blocks local Apply only; BaaS has no supported stop. */
  cancellation?: 'local-only';
  workflowId?: string;
}
export interface WorkflowAiWaitOptions<T> {
  signal?: AbortSignal;
  intervalMs?: number;
  timeoutMs?: number;
  onUpdate?: (job: T) => void;
}
export interface WorkflowAuthoringFile { path: string; contentBase64: string; mode: '100644' | '100755' }
export interface WorkflowAuthoringSnapshot {
  workflowId: string; packId: string; packPath?: string; baseRef: string; baseCommit: string;
  draftHead: string | null; versionFiles: WorkflowAuthoringFile[];
}
export interface WorkflowAuthoringBaseline {
  workflowId: string; userId: string; packId: string; rawPackId: string | null; draftToken: string;
  spec: WorkflowAuthoringSpec; specJson: string; sourceVersion: 'draft' | 'current';
  sourceCommit: string; expectedSavedHead: string | null;
}
export interface WorkflowPublishChange {
  path: string;
  operation: 'create' | 'replace' | 'delete';
  before: string | null;
  after: string | null;
  beforeMode?: '100644' | '100755' | null;
  afterMode?: '100644' | '100755' | null;
  beforeEncoding?: 'utf-8' | 'base64';
  afterEncoding?: 'utf-8' | 'base64';
}
export interface WorkflowPublishReview {
  workflowId: string; packId: string; candidateCommit: string; draftToken: string;
  expectedActiveCommit: string; changes: WorkflowPublishChange[];
}
export interface WorkflowSaveInput {
  baseline: WorkflowAuthoringBaseline;
  workflowId: string;
  spec: WorkflowAuthoringSpec;
  packId?: string;
  note?: string;
  facade?: { command?: string; remark?: string };
}
export interface WorkflowSaveResult {
  ok: true; saved: true; dbSaved: true; gitSaved: true;
  workflowId: string; packId: string; branch: string; commit: string;
  version?: number;
  spec: WorkflowAuthoringSpec;
  draftToken: string;
  requestId: string;
  baseline: WorkflowAuthoringBaseline;
}
export interface WorkflowPublishInput { workflowId: string; packId?: string; note?: string; receipt: WorkflowSaveResult; review: WorkflowPublishReview; confirmedFileDiff: true }
export interface WorkflowPublishResult {
  ok: true; deployed: true; workflowId: string; packId: string;
  deployNumber: number; version: number; tagName: string; messageId?: string | null;
}
/** A successful DB write is not a successful Git save; callers must keep the warning visible. */
export class WorkflowAuthoringPartialSaveError extends Error {
  readonly dbSaved = true;
  readonly gitSaved = false;
  readonly cause: unknown;
  constructor(readonly workflowId: string, readonly spec: WorkflowAuthoringSpec, cause: unknown) {
    super('数据库已保存，但 Git 快照未确认；请人工核对，勿盲目重试或发布。');
    this.name = 'WorkflowAuthoringPartialSaveError';
    this.cause = cause;
  }
}

/** Receipt-less transport failures never prove a mutation did not commit. */
export class WorkflowAuthoringOutcomeError extends Error {
  get creationUncertain(): boolean { return this.operation === 'create'; }
  constructor(readonly operation: 'create' | 'save' | 'publish', readonly workflowId: string,
    readonly requestId: string, readonly cause: unknown) {
    super(`${operation} 结果尚未完成校验，数据库或 Git 可能已变更；保留草稿并人工核对，勿盲目重试。`);
    this.name = 'WorkflowAuthoringOutcomeError';
  }
}
export class WorkflowAiRecoveryError extends Error {
  constructor(readonly job: WorkflowAiDraftJob, readonly cause: unknown) {
    super('AI 提交结果未确认；请用保留的同一草稿恢复轮询，不会重复派发。');
    this.name = 'WorkflowAiRecoveryError';
  }
}
