// 群头部 hook 的静态边收口（Task 17 源切片类型完备后的任务级红线延续）：
// useGroupChatHeaderPanels 属 collab 保留链，不得运行时引用任务副屏 bootstrap；
// 对 chat 侧 header hook（useChatHeaderPanels）不得有值 import——断言正则按 (?!type\b) 只禁
// 值 import、容忍 type-only 边；历史搜索模型已迁出（经 conversationSearchService 纯模型
// re-export 供给），现该 hook 对 useChatHeaderPanels 实际零 import（值或 type 均无）。
import { describe, expect, it } from '@jest/globals';
import { readFileSync } from 'fs';
import { join } from 'path';

const source = readFileSync(
  join(__dirname, '../../../../src/pages/Workspace/hooks/useGroupChatHeaderPanels.ts'),
  'utf8',
);

describe('useGroupChatHeaderPanels 静态边', () => {
  it('不再 import 任务副屏 bootstrap（该模块拉入 useTaskSwitcherTasks + services/myTask）', () => {
    expect(source).not.toMatch(/from\s+['"]@\/hooks\/useSessionTaskPanelBootstrap['"]/);
  });

  it('副屏重放助手改从纯模块 sidePaneContent 精确引入', () => {
    expect(source).toMatch(/from\s+['"]@\/pages\/Workspace\/Chat\/hooks\/sidePaneContent['"]/);
    expect(source).not.toMatch(/^import\s+(?!type\b)[^\n]*useChatHeaderPanels['"]/m);
  });
});
