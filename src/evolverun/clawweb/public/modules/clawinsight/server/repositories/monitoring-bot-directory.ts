import type { IDatabase, Row } from '@avernet/clawweb-shared/server/db';
import { MonitoringError, type MonitoringTarget } from '../services/monitoring/contracts.js';
import type { DirectoryBot, DirectoryScope, DirectorySearch, MonitoringBotDirectory } from '../services/monitoring/directory-contracts.js';
import { target } from '../services/monitoring/target.js';

/** Adapter for the existing, explicitly supplied ac_bots connection. Never falls back to monitoring DB. */
export class SqlMonitoringBotDirectory implements MonitoringBotDirectory {
  constructor(private readonly db: Pick<IDatabase, 'query' | 'dbType'>, private readonly tenantColumn: boolean) {}
  private exactColumn(column: string): string {
    return this.db.dbType === 'sqlite' ? `${column} COLLATE BINARY` : `BINARY ${column}`;
  }
  private scope(scope: DirectoryScope): { where: string[]; params: unknown[] } {
    if (!scope.tenant || !scope.allowedTargetEnvs.length || scope.allowedTargetEnvs.length > 20) throw new Error('Missing trusted directory scope');
    const where = ['is_delete = 0', `${this.exactColumn('env')} IN (${scope.allowedTargetEnvs.map(() => '?').join(',')})`];
    const params: unknown[] = [...scope.allowedTargetEnvs];
    if (this.tenantColumn) { where.push(`${this.exactColumn('avernet_tenant')} = ?`); params.push(scope.tenant); }
    return { where, params };
  }
  private async rows(where: string[], params: unknown[], limit: number): Promise<DirectoryBot[]> {
    try {
      const rows = await this.db.query(`SELECT CAST(id AS ${this.db.dbType === 'sqlite' ? 'TEXT' : 'CHAR'}) AS directory_id, bot_name, bot_id, entity_id, env, owner_id, owner_name
        FROM ac_bots WHERE ${where.join(' AND ')} ORDER BY id DESC LIMIT ?`, [...params, limit]);
      return rows.map(this.decode);
    } catch { throw new MonitoringError('NOT_READY', 'Bot 目录暂不可用或目录数据不完整。'); }
  }
  private decode(row: Row): DirectoryBot {
    if (!/^\d+$/.test(String(row.directory_id)) || typeof row.owner_id !== 'string' || !row.owner_id.trim()) throw new Error('Invalid directory row');
    return { ...target({ botId: row.bot_id as string, entityId: row.entity_id as string, env: row.env as string }),
      directoryId: String(row.directory_id), botName: typeof row.bot_name === 'string' && row.bot_name.trim() ? row.bot_name : '未命名 Bot',
      ownerId: row.owner_id, ownerName: row.owner_name == null ? null : String(row.owner_name) };
  }
  async exact(botId: string, scope: DirectoryScope): Promise<DirectoryBot[]> {
    const { where, params } = this.scope(scope);
    where.push(`${this.exactColumn('bot_id')} = ?`); params.push(botId);
    // Two are sufficient to prove ambiguity; never load all same-name defaults.
    return this.rows(where, params, 2);
  }
  async get(t: MonitoringTarget, scope: DirectoryScope): Promise<DirectoryBot | null> {
    const { where, params } = this.scope(scope);
    for (const [column, value] of [['bot_id', t.botId], ['entity_id', t.entityId], ['env', t.env]]) {
      where.push(`${this.exactColumn(column)} = ?`); params.push(value);
    }
    const rows = await this.rows(where, params, 2);
    if (rows.length > 1) throw new MonitoringError('NOT_READY', '目录目标不唯一。');
    return rows[0] ?? null;
  }
  async search(query: DirectorySearch) {
    const { where, params } = this.scope(query.principal);
    if (!Number.isInteger(query.limit) || query.limit < 1 || query.limit > 50) throw new Error('Invalid directory batch');
    if (query.scope === 'mine' || !query.principal.isClawInsightAdmin) {
      where.push(`${this.exactColumn('owner_id')} = ?`); params.push(query.principal.staffId);
    }
    if (query.q) {
      const fields = ['bot_name', 'bot_id', ...(query.principal.isClawInsightAdmin ? ['owner_id'] : [])];
      where.push(`(${fields.map(c => `LOWER(${c}) LIKE ? ESCAPE '!'`).join(' OR ')})`);
      const pattern = `%${query.q.toLowerCase().replace(/[!%_]/g, '!$&')}%`;
      params.push(...fields.map(() => pattern));
    }
    if (query.after !== null) {
      if (!/^\d+$/.test(query.after)) throw new MonitoringError('INVALID_EVENT', '目录分页位置无效。');
      where.push('id < ?'); params.push(query.after);
    }
    const items = await this.rows(where, params, query.limit + 1);
    return { items: items.slice(0, query.limit), hasMore: items.length > query.limit };
  }
}
