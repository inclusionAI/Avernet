import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '../db.js';

it('queues concurrent transactions and excludes unrelated writes from rollback', async () => {
  const raw = new Database(':memory:');
  raw.exec('CREATE TABLE items (value TEXT)');
  const db = new SqliteDatabase(raw);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const rolledBack = db.transaction(async tx => {
    await tx.exec('INSERT INTO items VALUES (?)', ['rolled-back']);
    await gate;
    throw new Error('rollback');
  });
  const independent = db.exec('INSERT INTO items VALUES (?)', ['retained']);
  const next = db.transaction(async tx => { await tx.exec('INSERT INTO items VALUES (?)', ['next']); });
  release();
  const results = await Promise.allSettled([rolledBack, independent, next]);
  expect(results.map(result => result.status)).toEqual(['rejected', 'fulfilled', 'fulfilled']);
  expect(await db.query('SELECT value FROM items ORDER BY value')).toEqual([{ value: 'next' }, { value: 'retained' }]);
  await db.close();
});
