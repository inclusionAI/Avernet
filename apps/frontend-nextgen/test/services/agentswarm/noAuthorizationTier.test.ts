import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The authorization tier has no gateway counterpart in this version, so it must
 * not exist in the port. This guards the negative space: a future port of the
 * PRD demo's approval screen should fail here rather than ship a flow whose
 * states the server can never report.
 */
const ROOTS = [
  'src/services/agentswarm',
  'src/domain/agentswarm',
  'src/components/agentswarm',
  'src/pages/AgentCompute',
];
const SINGLE_FILES = ['src/hooks/useRunPolling.ts', 'src/hooks/useRunList.ts', 'src/hooks/useRunDetail.ts'];

const REMOVED_STATES = ['WAITING_AUTHORIZATION', 'AUTHORIZATION_CANCELLED', 'STARTING'];
const REMOVED_MODULES = ['ComputeAuthorization', 'detectComputeIntent'];

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const files = [...ROOTS.flatMap(walk), ...SINGLE_FILES];

test('the source set under guard is not empty', () => {
  expect(files.length).toBeGreaterThan(5);
});

test.each(REMOVED_STATES)('%s does not appear anywhere in the port', (state) => {
  const offenders = files.filter((file) => readFileSync(file, 'utf8').includes(state));
  expect(offenders).toEqual([]);
});

test.each(REMOVED_MODULES)('%s was not ported', (module) => {
  const offenders = files.filter((file) => readFileSync(file, 'utf8').includes(module));
  expect(offenders).toEqual([]);
});

test('no agentswarm component reads a per-agent approval gate into the UI', () => {
  const uiFiles = files.filter((file) => file.includes('/components/') || file.includes('/AgentCompute/'));
  expect(uiFiles.length).toBeGreaterThan(0);
  for (const file of uiFiles) {
    expect(readFileSync(file, 'utf8')).not.toMatch(/\.authorization\b/);
  }
});

test('no run state is persisted on the client; the gateway owns run truth', () => {
  const offenders = files.filter((file) => readFileSync(file, 'utf8').includes('localStorage'));
  expect(offenders).toEqual([]);
});