import { vi } from 'vitest';
import type { BotDiagnosisRecoveryConfigStore } from '../../../monitoring/bot-config-contracts.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { BotDiagnosisRecoveryConfigRepository } from '../../../../repositories/bot-diagnosis-recovery-config-repository.js';
export async function enableRecoveryFixture(db: IDatabase) {
  await db.exec('CREATE TABLE IF NOT EXISTS ac_bots (bot_id TEXT, entity_id TEXT, owner_id TEXT, env TEXT, is_delete INTEGER DEFAULT 0)');
  await db.exec("INSERT INTO ac_bots (bot_id, entity_id, owner_id, env) VALUES ('default', 'owner', 'owner', 'prod')");
  await new BotDiagnosisRecoveryConfigRepository(db).setSwitches({ botId: 'default', entityId: 'owner', env: 'prod' },
    { diagnosisEnabled: true, recoveryEnabled: true, expectedVersion: 0, updatedBy: 'owner' });
}
export function notificationBotConfig(configJson = '{"notifications":{"owner":{"enabled":true},"group":{"enabled":true,"route_id":"test"}}}') {
  const row = { botId: 'bot', entityId: 'owner', env: 'prod', diagnosisEnabled: true, recoveryEnabled: true, configJson, version: 1 };
  return { resolveTargetForOwner: vi.fn(async () => ({ botId: row.botId, entityId: row.entityId, env: row.env })), find: vi.fn<BotDiagnosisRecoveryConfigStore['find']>(async () => row), findMany: vi.fn(async () => [row]), findForOwner: vi.fn(async () => row), setSwitches: vi.fn(), replaceConfiguration: vi.fn() } satisfies BotDiagnosisRecoveryConfigStore;
}
