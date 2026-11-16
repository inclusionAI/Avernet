import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { MonitoringError, type MonitoringTarget } from '../services/monitoring/contracts.js';
import type { BotDiagnosisRecoveryConfig, BotDiagnosisRecoveryConfigStore } from '../services/monitoring/bot-config-contracts.js';
import { targetKey } from '../services/monitoring/target.js';

type Row = { bot_id: string; entity_id: string; env: string; diagnosis_enabled: number;
  recovery_enabled: number; config_json: string; version: number };
const columns = 'bot_id, entity_id, env, diagnosis_enabled, recovery_enabled, config_json, version';
const predicate = '(bot_id = ? AND entity_id = ? AND env = ?)';
const params = (t: MonitoringTarget) => [t.botId, t.entityId, t.env];
const project = (r: Row): BotDiagnosisRecoveryConfig => ({ botId: r.bot_id, entityId: r.entity_id, env: r.env,
  diagnosisEnabled: r.diagnosis_enabled === 1, recoveryEnabled: r.recovery_enabled === 1,
  configJson: r.config_json, version: r.version });

export class BotDiagnosisRecoveryConfigRepository implements BotDiagnosisRecoveryConfigStore {
  constructor(private readonly db: IDatabase) {}
  async findForOwner(target: { botId: string; ownerId: string; env: string }): Promise<BotDiagnosisRecoveryConfig | null> {
    // Recovery requests carry Owner IDs. Resolve the current directory entity rather than assuming they are identical.
    const exact = (column: string) => this.db.dbType === 'sqlite' ? `${column} COLLATE BINARY` : `BINARY ${column}`;
    const bots = await this.db.query<{ entity_id: string }>(`SELECT entity_id FROM ac_bots WHERE is_delete = 0
      AND ${exact('bot_id')} = ? AND ${exact('owner_id')} = ? AND ${exact('env')} = ? LIMIT 2`, [target.botId, target.ownerId, target.env]);
    if (bots.length !== 1) return null;
    return this.find({ botId: target.botId, entityId: bots[0].entity_id, env: target.env });
  }
  async find(target: MonitoringTarget): Promise<BotDiagnosisRecoveryConfig | null> {
    return (await this.findMany([target]))[0];
  }
  async findMany(targets: readonly MonitoringTarget[]): Promise<Array<BotDiagnosisRecoveryConfig | null>> {
    const found = new Map<string, BotDiagnosisRecoveryConfig>();
    for (let i = 0; i < targets.length; i += 100) {
      const batch = targets.slice(i, i + 100);
      const rows = await this.db.query<Row>(`SELECT ${columns} FROM ce_bot_diagnosis_recovery_config WHERE ${batch.map(() => predicate).join(' OR ')}`, batch.flatMap(params));
      for (const row of rows) { const value = project(row); found.set(targetKey(value), value); }
    }
    return targets.map(t => found.get(targetKey(t)) ?? null);
  }
  async setSwitches(target: MonitoringTarget, input: { diagnosisEnabled: boolean; recoveryEnabled: boolean; expectedVersion: number; updatedBy: string }): Promise<BotDiagnosisRecoveryConfig> {
    const now = this.db.dialect.now();
    if (input.expectedVersion === 0) {
      try {
        await this.db.exec(`INSERT INTO ce_bot_diagnosis_recovery_config
          (bot_id, entity_id, env, diagnosis_enabled, recovery_enabled, config_json, version, updated_by, gmt_create, gmt_modified)
          VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)`,
        [...params(target), Number(input.diagnosisEnabled), Number(input.recoveryEnabled), '{}', input.updatedBy, now, now]);
      } catch (error) {
        if (await this.find(target)) throw new MonitoringError('EVENT_CONFLICT', '配置已更新，请刷新后重试。');
        throw error;
      }
    } else {
      const result = await this.db.exec(`UPDATE ce_bot_diagnosis_recovery_config SET diagnosis_enabled = ?, recovery_enabled = ?,
        version = version + 1, updated_by = ?, gmt_modified = ? WHERE ${predicate} AND version = ?`,
      [Number(input.diagnosisEnabled), Number(input.recoveryEnabled), input.updatedBy, now, ...params(target), input.expectedVersion]);
      if (!result.affectedRows) throw new MonitoringError('EVENT_CONFLICT', '配置已更新，请刷新后重试。');
    }
    return (await this.find(target))!;
  }
}
