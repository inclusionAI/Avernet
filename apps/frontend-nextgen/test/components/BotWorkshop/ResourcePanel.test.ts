import { buildVisibleResourceTree } from '@/components/BotWorkshop/Editor/ResourcePanel';
import type { BotEditorResource } from '@/domain/botEditor';

const resources: BotEditorResource[] = [
  { path: 'config', parentPath: '', name: 'config', type: 'folder' },
  { path: '.claude/skills-local', parentPath: '', name: 'skills-local', type: 'folder' },
  { path: 'config/runtime', parentPath: 'config', name: 'runtime', type: 'folder' },
  { path: 'config/runtime/app.json', parentPath: 'config/runtime', name: 'app.json', type: 'file' },
];

test('根目录响应中的多段 path 仍作为一级节点展示', () => {
  expect(buildVisibleResourceTree(resources, []).map(({ item, depth }) => [item.path, depth])).toEqual([
    ['config', 0],
    ['.claude/skills-local', 0],
  ]);
});

test('展开目录后按请求父级逐层合并子节点', () => {
  expect(
    buildVisibleResourceTree(resources, ['config', 'config/runtime']).map(({ item, depth }) => [item.path, depth]),
  ).toEqual([
    ['config', 0],
    ['config/runtime', 1],
    ['config/runtime/app.json', 2],
    ['.claude/skills-local', 0],
  ]);
});
