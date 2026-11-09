import fs from 'node:fs';
import path from 'node:path';
const root = path.resolve(__dirname, '../../../..');
function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(item => {
    if (['generated', '.umi', '.umi-production'].includes(item.name)) return [];
    const target = path.join(dir, item.name);
    return item.isDirectory() ? sourceFiles(target) : /\.[cm]?[jt]sx?$/.test(item.name) ? [target] : [];
  });
}
it('removes the old task UI directory instead of retaining compatibility re-exports', () => {
  expect(fs.existsSync(path.join(root, 'src/assets/TaskPanel'))).toBe(false);
  expect(fs.existsSync(path.join(root, 'test/assets/TaskPanel'))).toBe(false);
});
it('production sources use the built asset contract, never task-panel source or test aliases', () => {
  const forbidden = /(?:from\s*|import\s*\(|require\s*\()\s*['"][^'"]*(?:assets\/TaskPanel|(?:\.\.|frontend-nextgen)\/assets\/task-panel|generated\/task-panel|@task-panel-test)/;
  const violations = sourceFiles(path.join(root, 'src')).filter(file => forbidden.test(fs.readFileSync(file, 'utf8')));
  expect(violations.map(file => path.relative(root, file))).toEqual([]);
});
it('does not silently restore the deleted TaskPanel import path through a Jest alias', () => {
  expect(fs.readFileSync(path.join(root, 'jest.config.cjs'), 'utf8')).not.toContain('^@/assets/TaskPanel');
});

it('host source/build inputs contain no generated task assets or preparation hook', () => {
  for (const rel of ['src/generated/task-panel', 'public/task-panel-assets', 'scripts/prepare-task-panel.mjs']) {
    expect(fs.existsSync(path.join(root, rel))).toBe(false);
  }
  const scripts = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).scripts;
  expect(JSON.stringify(scripts)).not.toMatch(/task-panel:prepare|prepare-task-panel/);
});
it('the Docker frontend build does not copy or compile the independent task asset', () => {
  const dockerfile = fs.readFileSync(path.resolve(root, '../../docker/services/frontend-nextgen.dockerfile'), 'utf8');
  expect(dockerfile).not.toMatch(/(?:backend|frontend-nextgen)\/assets\/task-panel|--from=task-panel/);
  const dockerignore = fs.readFileSync(path.resolve(root, '../../.dockerignore'), 'utf8');
  expect(dockerignore.split(/\r?\n/)).toContain('apps/frontend-nextgen/assets/task-panel');
});
it('the shared package is consumed through type-only imports, never bundled into the host', () => {
  const violations = sourceFiles(path.join(root, 'src')).filter(file => {
    const source = fs.readFileSync(file, 'utf8');
    return [...source.matchAll(/(?:import|export)\s+(?:[^;]*?)from\s+['"]@avernet-assets\/task-panel['"]/g)]
      .some(match => !/^(?:import|export)\s+type\b/.test(match[0]));
  });
  expect(violations).toEqual([]);
});
it('general rich content and file utilities are real local implementations with no task dependency', () => {
  for (const rel of ['components/Common/RichContent/index.ts', 'components/Common/RichContent/SafeHtml.tsx',
    'components/Common/RichContent/sanitizeHtml.ts', 'services/workspace/filePreview.ts', 'shared/taskStatus.ts']) {
    const source = fs.readFileSync(path.join(root, 'src', rel), 'utf8');
    expect(source).not.toMatch(/generated\/task-panel|@avernet-assets\/task-panel|backend\/assets/);
  }
});

it('does not retain the retired host-side task switching/navigation implementation', () => {
  for (const rel of ['components/Workspace/TaskPanelSwitcherSlot.tsx', 'hooks/useTaskSwitcherTasks.ts', 'hooks/taskPanelNavBinding.ts']) {
    expect(fs.existsSync(path.join(root, 'src', rel))).toBe(false);
  }
});
it('the task artifacts drawer mounts a complete view, not an internal fetcher/list pipeline', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/Lab/Tasks/TaskArtifactsDrawer.tsx'), 'utf8');
  expect(source).toContain('asset.TaskArtifactsView');
  expect(source).not.toMatch(/asset\.(?:TaskPanelFetcher|TaskArtifactsList)|includeActionLog|loading.*error.*retry/);
});

it('the host facade contains no second task navigation state machine or observer', () => {
  const store = fs.readFileSync(path.join(root, 'src/stores/taskSidePanelStore.ts'), 'utf8');
  expect(store).toContain('readonly activeTaskId');
  expect(store).not.toMatch(/navByTask|pendingNavigation|nodeScrollSignal|MAX_DRILL_DEPTH|setActiveTask|selectRootNode|drillInto/);
  expect(fs.existsSync(path.join(root, 'src/pages/Workspace/Chat/hooks/taskSessionNavigation.ts'))).toBe(false);
  expect(fs.existsSync(path.join(root, 'src/stores/taskUpdateAttentionStore.ts'))).toBe(false);
});

it('the real host gate rejects direct, normalized and runtime asset imports but allows public types', () => {
  const { spawnSync } = require('node:child_process');
  const os = require('node:os');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'task-panel-host-boundary-'));
  try {
    fs.mkdirSync(path.join(temporary, 'src'));
    for (const statement of [
      "import { TaskLoopView } from '../assets/task-panel/src';",
      "const asset = require('../assets/other/../task-panel/src');",
      "const asset = import('@avernet-assets/task-panel');",
      "import { createTaskPanelController } from '@avernet-assets/task-panel';",
    ]) {
      fs.writeFileSync(path.join(temporary, 'src/consumer.ts'), statement);
      const result = spawnSync(process.execPath, [path.join(root, 'scripts/ci-guardrails.mjs')], { cwd: temporary, encoding: 'utf8' });
      expect(result.status).not.toBe(0);
      expect(result.stderr).toContain('TC-G007');
    }
    fs.writeFileSync(path.join(temporary, 'src/consumer.ts'), "import type { TaskPanelController } from '@avernet-assets/task-panel';");
    const allowed = spawnSync(process.execPath, [path.join(root, 'scripts/ci-guardrails.mjs')], { cwd: temporary, encoding: 'utf8' });
    expect(allowed.status).toBe(0);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
