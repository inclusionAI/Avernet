/** Database-only current-value CAS for an existing draft; it does not publish. */
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { normalizeWorkflowSpec, validateWorkflowSemantics } from '../workflow.js';
import { validateSpec } from '../validators/workflow-validator.js';
import { validPackGitId } from './pack-git-prepared.js';
import { assertNoUnresolvedLegacyPackOperation } from './legacy-pack-operation-guard.js';
import { PackOperationError } from './pack-operation-service.js';
import { editorDraftToken } from './editor-draft-token.js';

export class EditorDraftCasError extends Error {
  constructor(public readonly status: number, public readonly code: string) {
    super(code);
    this.name = 'EditorDraftCasError';
  }
}
export type EditorDraftCasResult = { workflowId: string; packId: string | null; specJson: string; draftToken: string };
export type EditorDraftCasSaveInput = { workflowId: string; actorId: string; expectedDraftToken: string; spec: unknown; packId?: string };
type DraftRow = { id: number; workflow_id: string; pack_id: string | null; spec_json: string; version: number | null;
  title: string | null; owner_id: string | null; gmt_create: number | string; gmt_modified: number | string };
const fields = 'id, workflow_id, pack_id, spec_json, version, title, owner_id, gmt_create, gmt_modified';
const fail = (status: number, code: string): never => { throw new EditorDraftCasError(status, code); };
const validWorkflowId = (value: unknown): value is string => typeof value === 'string' && validPackGitId(value) && !/^__.+__$/.test(value);
const validActor = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.trim() === value && value !== '*' && value !== 'unknown';
const resultFor = (row: DraftRow): EditorDraftCasResult => ({ workflowId: row.workflow_id, packId: row.pack_id, specJson: row.spec_json, draftToken: editorDraftToken(row) });

export class EditorDraftCasService {
  constructor(private readonly db: IDatabase, private readonly botPermRepo: BotWorkflowPermissionRepository | null) {}
  private assertInput(workflowId: unknown, actorId: unknown): asserts workflowId is string {
    if (!validActor(actorId) || !this.botPermRepo) fail(403, 'FORBIDDEN');
    if (!validWorkflowId(workflowId)) fail(400, 'INVALID_REQUEST');
  }
  private async permission(db: IDatabase, workflowId: string, actorId: string, edit: boolean): Promise<void> {
    const repo = this.botPermRepo!.withDatabase(db);
    const allowed = await repo.hasEditPermission(workflowId, actorId)
      || (!edit && await repo.hasViewPermission(workflowId, actorId));
    if (!allowed) fail(403, 'FORBIDDEN');
  }
  private async row(db: IDatabase, workflowId: string, lock: boolean): Promise<DraftRow | null> {
    const suffix = lock && (db.dbType === 'mysql' || db.dbType === 'zdas') ? ' FOR UPDATE' : '';
    const rows = await db.query<DraftRow>(`SELECT ${fields} FROM workflow_specs WHERE workflow_id = ?${suffix}`, [workflowId]);
    if (rows.length > 1) fail(500, 'DRAFT_STORAGE_ERROR');
    const row = rows[0];
    // SQL collations may match distinct identifiers; never accept that match.
    if (!row || row.workflow_id !== workflowId) return null;
    if (!Number.isSafeInteger(row.id) || typeof row.spec_json !== 'string'
      || (row.pack_id !== null && typeof row.pack_id !== 'string')) fail(500, 'DRAFT_STORAGE_ERROR');
    return row;
  }
  async read(workflowId: string, actorId: string): Promise<EditorDraftCasResult> {
    this.assertInput(workflowId, actorId);
    try {
      await this.permission(this.db, workflowId, actorId, false);
      const row = await this.row(this.db, workflowId, false);
      if (!row) return fail(404, 'DRAFT_NOT_FOUND');
      return resultFor(row);
    } catch (error) {
      if (error instanceof EditorDraftCasError) throw error;
      return fail(500, 'DRAFT_STORAGE_ERROR');
    }
  }
  async save(input: EditorDraftCasSaveInput): Promise<EditorDraftCasResult> {
    if (!input || typeof input !== 'object') return fail(400, 'INVALID_REQUEST');
    this.assertInput(input.workflowId, input.actorId);
    if (typeof input.expectedDraftToken !== 'string' || !/^[a-f0-9]{64}$/.test(input.expectedDraftToken)
      || (Object.hasOwn(input, 'packId') && (typeof input.packId !== 'string' || !validPackGitId(input.packId)))) return fail(400, 'INVALID_REQUEST');
    let submitted: unknown;
    let specJson: string;
    try {
      specJson = JSON.stringify(input.spec);
      submitted = JSON.parse(specJson);
    } catch { return fail(400, 'INVALID_WORKFLOW'); }
    if (!submitted || typeof submitted !== 'object' || Array.isArray(submitted)
      || (submitted as { id?: unknown }).id !== input.workflowId) return fail(400, 'WORKFLOW_ID_MISMATCH');
    let title: string;
    try {
      const validation = validateSpec(submitted);
      if (!validation.valid || !validation.normalizedSpec) return fail(400, 'INVALID_WORKFLOW');
      const normalized = normalizeWorkflowSpec(validation.normalizedSpec);
      validateWorkflowSemantics(normalized);
      if (normalized.id !== input.workflowId) return fail(400, 'WORKFLOW_ID_MISMATCH');
      title = normalized.title || input.workflowId;
    } catch (error) {
      if (error instanceof EditorDraftCasError) throw error;
      return fail(400, 'INVALID_WORKFLOW');
    }
    if (!['sqlite', 'mysql', 'zdas'].includes(this.db.dbType)) return fail(503, 'DRAFT_STORAGE_UNAVAILABLE');
    try {
      return await this.db.transaction(async tx => {
        if (!['sqlite', 'mysql', 'zdas'].includes(tx.dbType) || tx.dbType !== this.db.dbType) return fail(503, 'DRAFT_STORAGE_UNAVAILABLE');
        const current = await this.row(tx, input.workflowId, true);
        await this.permission(tx, input.workflowId, input.actorId, true);
        if (!current || editorDraftToken(current) !== input.expectedDraftToken) return fail(409, 'DRAFT_CONFLICT');
        // A retirement preflight only: it cannot fence a legacy writer that starts later.
        await assertNoUnresolvedLegacyPackOperation(tx, input.workflowId);
        // Workflow edit permission alone does not authorize binding another Pack.
        if (input.packId !== undefined && input.packId !== current.pack_id) return fail(403, 'FORBIDDEN');
        if (current.spec_json === specJson) return resultFor(current);
        const updated = await tx.exec('UPDATE workflow_specs SET spec_json = ?, title = ?, owner_id = ?, gmt_modified = ? WHERE workflow_id = ? AND id = ?',
          [specJson, title, input.actorId, tx.dialect.now(), input.workflowId, current.id]);
        const actual = await this.row(tx, input.workflowId, true);
        // No-ops returned above; an unknown/zero-row changed write is not success.
        if (updated.affectedRows !== 1 || !actual || actual.id !== current.id || actual.pack_id !== current.pack_id
          || actual.spec_json !== specJson || actual.title !== title || actual.owner_id !== input.actorId) return fail(500, 'DRAFT_STORAGE_ERROR');
        return resultFor(actual);
      });
    } catch (error) {
      if (error instanceof EditorDraftCasError) throw error;
      if (error instanceof PackOperationError && error.code === 'LEGACY_PACK_OPERATION_UNRESOLVED') {
        return fail(409, 'LEGACY_PACK_OPERATION_UNRESOLVED');
      }
      return fail(500, 'DRAFT_STORAGE_ERROR');
    }
  }
}
