import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

it('BCS 状态机副屏由 manifest/CDN 远程加载，不在 Open Core 注册本地实现', () => {
  const localPanelPath = path.join(process.cwd(), 'src/assets/BcsWorkflowPanel/StateMachineRunView.tsx');
  expect(existsSync(localPanelPath)).toBe(false);

  const sourceFiles = [
    'src/assets/TaskPanel/index.ts',
    'src/services/workspace/groupTaskAdapter.ts',
    'src/services/bcs/libraryCdnInjector.ts',
    'src/services/bcs/UmdPanel.tsx',
  ];
  const source = sourceFiles.map((file) => readFileSync(path.join(process.cwd(), file), 'utf8')).join('\n');

  expect(source).toContain('bcsPanel.StateMachineRunView');
  expect(source).not.toContain('src/assets/BcsWorkflowPanel/StateMachineRunView');
});
