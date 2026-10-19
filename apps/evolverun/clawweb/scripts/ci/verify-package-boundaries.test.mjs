import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { verifyPackageBoundaries } from './verify-package-boundaries.mjs';

function fixture(t, source) {
  const root = mkdtempSync(join(tmpdir(), 'clawweb-boundaries-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const name of ['workflow', 'shared']) {
    mkdirSync(join(root, 'public', name), { recursive: true });
    writeFileSync(join(root, 'public', name, 'package.json'), JSON.stringify({ name: `@avernet/${name}` }));
  }
  const directory = join(root, 'public/workflow/web/__tests__');
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, 'sample.test.tsx'), source);
  return root;
}

for (const source of [
  "import { x } from '../../../shared/client'",
  "export { x } from '../../../shared/client'",
  "const x = import('../../../shared/client')",
  "vi.mock('@avernet/shared/client', () => import('../../../shared/client'))",
  "vi.mock('../../../shared/client', () => ({}))",
  "vi.importActual('../../../shared/client')",
  "require('../../../shared/client')",
]) {
  test(`rejects cross-package references in tests: ${source}`, (t) => {
    assert.throws(() => verifyPackageBoundaries(fixture(t, source)), /relative import escapes package/);
  });
}

test('accepts public package entry points and package-local relative imports', (t) => {
  const root = fixture(t, "import { x } from '@avernet/clawweb-shared/web/api/client'; import { y } from '../hooks'; vi.mock('../local')");
  assert.equal(verifyPackageBoundaries(root), 2);
});

test('rejects public imports of internal packages', (t) => {
  assert.throws(() => verifyPackageBoundaries(fixture(t, "import x from '@ocb/private'")), /imports OCB/);
});

test('resolves symlinked directories even when the imported file has not been generated', (t) => {
  const root = fixture(t, "import x from './linked/not-generated'");
  symlinkSync(join(root, 'public/shared'), join(root, 'public/workflow/web/__tests__/linked'), 'dir');
  assert.throws(() => verifyPackageBoundaries(root), /relative import escapes package/);
});

test('does not check generated output or installed dependencies', (t) => {
  const root = fixture(t, "import x from '../hooks'");
  for (const name of ['dist', 'dist-server', 'node_modules']) {
    const directory = join(root, 'public/workflow', name);
    mkdirSync(directory);
    writeFileSync(join(directory, 'generated.js'), "import x from '../../shared/client'");
  }
  assert.equal(verifyPackageBoundaries(root), 2);
});

test('fails closed when no packages are discovered', (t) => {
  const root = fixture(t, '');
  rmSync(join(root, 'public'), { recursive: true });
  mkdirSync(join(root, 'public'));
  assert.throws(() => verifyPackageBoundaries(root), /No public ClawWeb packages/);
});
