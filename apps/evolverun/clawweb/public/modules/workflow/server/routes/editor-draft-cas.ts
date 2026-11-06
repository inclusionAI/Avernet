import { Router, type Request } from 'express';
import { validPackGitId } from '../services/pack-git-prepared.js';

type EditorDraft = { workflowId: string; packId: string | null; specJson: string; draftToken: string };
type DraftWrite = { workflowId: string; actorId: string; expectedDraftToken: string; spec: unknown; packId?: string };
type Dependencies = {
  resolvePrincipal(req: Request): Promise<{ actorId: string } | null>;
  read(workflowId: string, actorId: string): Promise<EditorDraft>;
  save(input: DraftWrite): Promise<EditorDraft>;
};
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const allowedErrors = new Map<string, number>([
  ['INVALID_REQUEST', 400], ['INVALID_WORKFLOW', 400], ['WORKFLOW_ID_MISMATCH', 400],
  ['FORBIDDEN', 403], ['WORKFLOW_NOT_FOUND', 404], ['DRAFT_NOT_FOUND', 404], ['DRAFT_CONFLICT', 409], ['LEGACY_PACK_OPERATION_UNRESOLVED', 409],
]);

/** Mount behind a trusted host's authentication, CORS and CSRF middleware. */
export function createEditorDraftCasRouter(deps: Dependencies): Router {
  const router = Router();
  router.all('/:workflowId/editor-draft', async (req, res) => {
    res.set('Cache-Control', 'no-store');
    if (req.method !== 'GET' && req.method !== 'PUT') { res.status(405).json({ code: 'METHOD_NOT_ALLOWED' }); return; }
    try {
      const principal = await deps.resolvePrincipal(req);
      if (!principal || typeof principal.actorId !== 'string' || !principal.actorId.trim() || principal.actorId === 'unknown') {
        res.status(401).json({ code: 'AUTHENTICATION_REQUIRED' }); return;
      }
      const { workflowId } = req.params;
      if (!validPackGitId(workflowId) || /^__.+__$/.test(workflowId) || Object.keys(req.query).length) {
        res.status(400).json({ code: 'INVALID_REQUEST' }); return;
      }
      if (req.method === 'GET') { res.json(await deps.read(workflowId, principal.actorId)); return; }
      const body: unknown = req.body;
      if (!object(body) || Object.keys(body).some(key => !['expectedDraftToken', 'spec', 'packId'].includes(key))
        || typeof body.expectedDraftToken !== 'string' || !/^[a-f0-9]{64}$/.test(body.expectedDraftToken)
        || !object(body.spec) || (body.packId !== undefined && (typeof body.packId !== 'string' || !validPackGitId(body.packId)))) {
        res.status(400).json({ code: 'INVALID_REQUEST' }); return;
      }
      const input: DraftWrite = { workflowId, actorId: principal.actorId, expectedDraftToken: body.expectedDraftToken, spec: body.spec,
        ...(typeof body.packId === 'string' ? { packId: body.packId } : {}) };
      res.json(await deps.save(input));
    } catch (cause) {
      const failure = cause as { code?: unknown; status?: unknown } | null;
      const code = typeof failure?.code === 'string' ? failure.code : '';
      const status = allowedErrors.get(code);
      if (status !== undefined && failure?.status === status) { res.status(status).json({ code }); return; }
      res.status(503).json({ code: 'SERVICE_UNAVAILABLE' });
    }
  });
  return router;
}
