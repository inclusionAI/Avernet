import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

const digest = (text: string) => createHash('sha256').update(text).digest('hex');
const source = (file: string) => readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');

it('preserves the original historical migration declarations byte-for-byte and in order', () => {
  // Recorded from the pre-split schema.ts at d0cdd0234. Historical ordering contains
  // repeated/nonascending versions; splitting must not silently repair or reorder them.
  const historical = ['schema-v1-36.ts', 'schema-v37-74.ts', 'schema-v75-108.ts', 'schema-v109-143.ts']
    .map(file => source(file).split('readonly Migration[] = [\n')[1].split(/\];\n$/)[0]).join('');
  expect(digest(historical)).toBe('22144f7c9a3ca9441931695b3e08c81f8b44ad750c93134906dc49008dd34909');
  const assembly = source('schema.ts');
  expect(assembly.slice(assembly.indexOf('export const migrations:'))).toMatch(
    /migrationsV1To36,[\s\S]*migrationsV37To74,[\s\S]*migrationsV75To108,[\s\S]*migrationsV109To143,[\s\S]*repairDeployTagMigrations,[\s\S]*flowRunMarkersMigrations/);
  const start = assembly.indexOf('export const sqliteTriggers: string[] = [');
  const triggers = assembly.slice(start, assembly.indexOf('\n];', start) + 3);
  expect(digest(triggers)).toBe('d61129dc7bead583b2f5cdbeb8c3e5d8314821f0b7e7fb2dde0117da8249fc24');
});
