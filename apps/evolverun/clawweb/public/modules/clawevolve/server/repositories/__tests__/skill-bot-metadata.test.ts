import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { SkillAssetRepository } from '../skill-asset-repository.js';

describe('Skill Bot display metadata', () => {
  let db: SqliteDatabase;
  let botDb: SqliteDatabase;
  let repo: SkillAssetRepository;
  beforeEach(async () => {
    db = new SqliteDatabase(new Database(':memory:'));
    botDb = new SqliteDatabase(new Database(':memory:'));
    await botDb.exec(`CREATE TABLE ac_bots (
      id INTEGER PRIMARY KEY, bot_id TEXT, bot_name TEXT, owner_id TEXT, entity_id TEXT, is_delete INTEGER
    )`);
    await botDb.exec(`INSERT INTO ac_bots VALUES
      (1, 'selected', 'Old name', 'owner', 'owner', 0),
      (2, 'selected', 'Current name', 'owner', 'owner', 0),
      (3, 'unselected', 'Other Bot', 'another-owner', 'another-owner', 0),
      (4, 'deleted', 'Deleted Bot', 'owner', 'owner', 1)`);
    repo = new SkillAssetRepository(db, botDb);
  });
  afterEach(async () => { await db.close(); await botDb.close(); });

  it('queries only distinct requested IDs through the injected Bot database, newest first', async () => {
    const query = vi.spyOn(botDb, 'query');
    const rows = await repo.listBotMetadata(['selected', 'selected', 'deleted', 'missing']);
    expect(rows.map(row => row.bot_name)).toEqual(['Current name', 'Old name']);
    expect(query).toHaveBeenCalledTimes(1);
    expect(query).toHaveBeenCalledWith(expect.stringContaining('WHERE bot_id IN (?,?,?)'), ['selected', 'deleted', 'missing']);
    expect(rows.every(row => row.owner_id === 'owner')).toBe(true);
  });

  it('does not issue an unscoped query when there are no visible records', async () => {
    const query = vi.spyOn(botDb, 'query');
    expect(await repo.listBotMetadata([])).toEqual([]);
    expect(query).not.toHaveBeenCalled();
  });

  it('keeps metadata optional when the Bot database or table is unavailable', async () => {
    expect(await new SkillAssetRepository(db).listBotMetadata(['selected'])).toEqual([]);
    vi.spyOn(botDb, 'query').mockRejectedValue(new Error('Database unavailable'));
    expect(await repo.listBotMetadata(['selected'])).toEqual([]);
  });
});
