import { Router, type Request } from 'express';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { parseRunLink, registerRunLink, type AuthorizeRunLink, type LinkPrincipal } from '../../services/flow-run-link-registration.js';

/** A host implementation must authenticate independently of request body and bind
 * the credential to this bot and owner. Shared-key signature verification alone
 * is not such an implementation. */
export type RunLinkAuth = {
  authenticate: (request: Request) => Promise<LinkPrincipal | null>;
  authorize: AuthorizeRunLink;
};

export function createInternalFlowRunLinksRouter(db: IDatabase, auth?: RunLinkAuth): Router {
  const router = Router();
  router.post('/', async (req, res) => {
    res.set('Cache-Control', 'no-store');
    if (!auth || typeof auth.authenticate !== 'function' || typeof auth.authorize !== 'function' || !db || db.dbType === 'noop') {
      res.status(503).json({ error: 'Unavailable' }); return;
    }
    try {
      const principal = await auth.authenticate(req);
      if (!principal || typeof principal.botId !== 'string' || !principal.botId.trim()
          || typeof principal.ownerId !== 'string' || !principal.ownerId.trim()) {
        res.status(401).json({ error: 'Unauthorized' }); return;
      }
      const link = parseRunLink(req.body);
      if (!link || Object.keys(req.query).length) { res.status(400).json({ error: 'Invalid request' }); return; }
      const result = await registerRunLink(db, principal, link, auth.authorize);
      if (result !== 'ok') { res.status(404).json({ error: 'Not found' }); return; }
      res.status(204).end();
    } catch {
      res.status(503).json({ error: 'Unavailable' });
    }
  });
  return router;
}
