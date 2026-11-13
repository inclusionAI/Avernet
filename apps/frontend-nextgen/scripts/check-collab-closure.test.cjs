const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { checkClosure, extractEdges, extractLocalEdges } = require('./check-collab-closure.cjs');

function fixture(source, extra = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'collab-closure-'));
  const files = {
    'tsconfig.json': JSON.stringify({ compilerOptions: {
      baseUrl: '.', paths: { '@/*': ['./src/*'] }, moduleResolution: 'node',
    } }),
    'config/routes.collab.ts': "export const routes = [{ component: '@/pages/Keep' }];",
    'src/app.tsx': 'export {};',
    'src/layouts/AppLayout.tsx': 'export default function AppLayout() {}',
    'src/pages/Keep.tsx': source,
    'src/pages/BotWorkshop/index.tsx': 'export interface Bot {}',
    'src/shared/allowed.ts': 'export const allowed = true;',
    ...extra,
  };
  try {
    for (const [name, content] of Object.entries(files)) {
      const file = path.join(root, name);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, content);
    }
    return checkClosure(root);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

for (const source of [
  "import '@/pages/BotWorkshop';",
  "const Page = lazy(() => import('@/pages/BotWorkshop'));",
  "const Page = require('@/pages/BotWorkshop');",
  "export * from '@/pages/BotWorkshop';",
  "import '@/shared/bridge';",
]) {
  test(`rejects forbidden runtime edge: ${source}`, () => {
    const result = fixture(source, { 'src/shared/bridge.ts': "export * from '@/pages/BotWorkshop';" });
    assert.ok(result.failures.some((failure) => failure.includes('src/pages/BotWorkshop/')));
  });
}

test('erases type-only edges and follows allowed dynamic modules', () => {
  const result = fixture(`
    import type { Bot } from '@/pages/BotWorkshop';
    export type { Bot as Other } from '@/pages/BotWorkshop';
    import { type Bot as Inline } from '@/pages/BotWorkshop';
    const allowed = import('@/shared/allowed');
  `);
  assert.deepEqual(result.failures, []);
  // 源切片文件集（Task 17 新增返回面）：type-only 边不把目标文件带入 collab 运行时文件集。
  assert.ok(Array.isArray(result.visitedFiles));
  assert.ok(result.visitedFiles.every((file) => !file.startsWith('src/pages/BotWorkshop/')));
  assert.ok(result.visitedFiles.includes('src/shared/allowed.ts'));
});

// collab-form Task 17：源切片边分类固定装置——运行时守卫与源切片清单共用同一 parser。
test('extractLocalEdges classifies runtime / type / asset / re-export / dynamic import', () => {
  const { edges, issues } = extractLocalEdges('src/demo/slice.ts', `
    import value from './a';
    import * as ns from './a';
    import { A } from './a';
    import type { T } from './b';
    import { type Only } from './c';
    export type { Re } from './d';
    import defaultStyles from './styles.css';
    import './side-effect.css';
    export * from './reexport';
    const target = import('./dyn');
    const bundled = require('./req');
  `);
  assert.deepEqual(issues, []);
  assert.deepEqual(edges, [
    { specifier: './a', kind: 'runtime' },
    { specifier: './b', kind: 'type' },
    { specifier: './c', kind: 'type' },
    { specifier: './d', kind: 'type' },
    { specifier: './styles.css', kind: 'asset' },
    { specifier: './side-effect.css', kind: 'asset' },
    { specifier: './reexport', kind: 'runtime' },
    { specifier: './dyn', kind: 'runtime' },
    { specifier: './req', kind: 'runtime' },
  ]);
});

test('extractEdges runtime view keeps runtime and asset edges and drops type edges', () => {
  const { edges, issues } = extractEdges('src/demo/slice.ts', `
    import { A } from './a';
    import type { T } from './b';
    export type { Re } from './d';
    import styles from './styles.css';
  `);
  assert.deepEqual(issues, []);
  assert.deepEqual(edges, ['./a', './styles.css']);
});

test('rejects missing local code', () => {
  assert.ok(fixture("import '@/shared/missing';").failures.some((f) => f.includes('unresolved code import')));
});

test('rejects opaque bundled imports', () => {
  assert.ok(fixture('const page = import(target);').failures.some((f) => f.includes('nonliteral')));
});

test('unreviewed runtime URL imports cannot silently bypass the guard', () => {
  assert.ok(fixture('const page = import(/* webpackIgnore: true */ url);').failures.some((f) => f.includes('explicit reviewed exception')));
});

test('exact facade alias does not rewrite registry subpaths', () => {
  const result = fixture("import '@/extensions'; import '@/extensions/slots';", {
    'src/extensions/collab.ts': 'export {};',
    'src/extensions/slots.ts': 'export {};',
    'src/extensions/empty.ts': "import '@/pages/BotWorkshop';",
    'src/extensions/index.ts': "export * from './empty';",
  });
  assert.deepEqual(result.failures, []);
});

for (const page of ['Bots', 'Groups']) {
  test(`collab discovery allows only the ${page} page entry`, () => {
    const result = fixture(`import '@/pages/CollaborationSquare/${page}';`, {
      [`src/pages/CollaborationSquare/${page}/index.tsx`]: 'export {};',
    });
    assert.deepEqual(result.failures, []);
  });
}

for (const forbidden of [
  'src/services/tasks/panel/taskPanelRuntime.ts',
  'src/components/Workspace/TaskPanelAdapter.tsx',
  'src/components/Workspace/TaskPanelAssetBoundary.tsx',
  'src/hooks/useGroupTaskPanelBootstrap.ts',
  'src/hooks/useSharedTaskPanelRecovery.ts',
  'src/pages/CollaborationSquare/Experts/index.tsx',
  'src/pages/CollaborationSquare/Tasks/index.tsx',
  'src/pages/CollaborationSquare/Bots/Hidden.tsx',
  'src/components/CollaborationSquare/SquarePageShell/index.tsx',
  'src/hooks/useCollaborationSquareTask.ts',
  'src/services/collaborationSquare/collaborationSquareService.ts',
  'src/services/backendApi/collaboration/bbsTaskController.ts',
]) {
  test(`allowed discovery entry cannot pull ${forbidden} into collab`, () => {
    const specifier = `@/${forbidden.slice(4).replace(/\.(tsx?|jsx?)$/, '')}`;
    const result = fixture("import '@/pages/CollaborationSquare/Bots';", {
      'src/pages/CollaborationSquare/Bots/index.tsx': `export * from '${specifier}';`,
      [forbidden]: 'export {};',
    });
    assert.ok(result.failures.some((failure) => failure.includes(forbidden)));
  });
}
