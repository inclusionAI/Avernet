import { createRequire } from 'node:module';
import path from 'node:path';

const requireCjs = createRequire(__filename);
const { checkClosure } = requireCjs('../../scripts/check-collab-closure.cjs');

it('collab 导出包含两个公开发现页面，但不包含专家、任务广场或单聊实现', () => {
  const result = checkClosure(path.resolve(__dirname, '../..'));
  expect(result.failures).toEqual([]);
  expect(result.visitedFiles).toEqual(expect.arrayContaining([
    'src/pages/CollaborationSquare/Bots/index.tsx',
    'src/pages/CollaborationSquare/Groups/index.tsx',
  ]));
  const forbidden = [
    'src/pages/CollaborationSquare/Experts/',
    'src/pages/CollaborationSquare/Tasks/',
    'src/components/CollaborationSquare/PublicTaskCatalogPanel/',
    'src/hooks/useCollaborationSquareTask.ts',
    'src/services/backendApi/collaboration/bbsTaskController.ts',
    'src/pages/Workspace/Chat/index.tsx',
  ];
  expect(result.visitedFiles.filter((file: string) => forbidden.some((prefix) => file.startsWith(prefix)))).toEqual([]);
});
