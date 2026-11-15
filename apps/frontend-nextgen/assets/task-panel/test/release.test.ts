// @vitest-environment node
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const root = new URL('../', import.meta.url);
const pkg = JSON.parse(readFileSync(new URL('package.json', root), 'utf8'));

describe('task-specific publishing contract', () => {
  it('uses public registry metadata and versioned UMD/declaration entry points', () => {
    expect(pkg.name).toBe('@avernet-assets/task-panel');
    expect(pkg.repository).toEqual({ type: 'git', url: 'git+https://github.com/inclusionAI/Avernet.git', directory: 'apps/frontend-nextgen/assets/task-panel' });
    expect(pkg.publishConfig).toEqual({ registry: 'https://registry.npmjs.org/', access: 'public' });
    for (const entry of ['main', 'browser', 'unpkg', 'jsdelivr']) expect(pkg[entry]).toBe('./dist/index.umd.js');
    expect(pkg.types).toBe('./dist/public-api.d.ts');
    expect(pkg.scripts.prepack).toBe('npm run verify');
    expect(pkg.files).toContain('dist');
    expect(pkg.files).not.toContain('src');
  });

  it('publishes only runtime outputs, declarations and delivery documentation', () => {
    expect(pkg.files).toEqual(['dist', 'README.md', 'PUBLISHING.md', 'LICENSE', 'scripts/serve-assets.mjs']);
    expect(pkg.scripts.verify).toContain('npm run test:umd');
    expect(pkg.scripts.verify).toContain('npm run test:delivery');
    expect(pkg.scripts.verify).toContain('npm run scan:public');
  });
});
