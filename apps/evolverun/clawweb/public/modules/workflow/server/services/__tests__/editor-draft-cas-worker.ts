/** Run in a separate process so a SQLite busy wait cannot block the winning writer. */
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { EditorDraftCasService } from '../editor-draft-cas-service.js';

const [filename, token, title] = process.argv.slice(2);
const raw = new Database(filename, { timeout: 5000 });
const db = new SqliteDatabase(raw);
const service = new EditorDraftCasService(db, new BotWorkflowPermissionRepository(db));
const initial = await service.read('flow', 'editor');
process.send?.({ status: 'ready' });
await new Promise<void>(resolve => process.once('message', () => resolve()));
try {
  await service.save({ workflowId: 'flow', actorId: 'editor', expectedDraftToken: token,
    spec: { ...JSON.parse(initial.specJson), title } });
  process.send?.({ status: 'ok' });
} catch (error) {
  process.send?.({ status: 'error', code: (error as { code?: string }).code });
}
await db.close();
process.disconnect?.();
