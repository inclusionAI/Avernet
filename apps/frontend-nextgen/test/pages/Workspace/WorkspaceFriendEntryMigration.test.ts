import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

describe('Workspace 好友入口迁移', () => {
  it('移除添加好友弹窗和重复目录状态机，只在协作群工具行保留发起协作', () => {
    const root = process.cwd();
    // Task 10 退休旧 /workspace 混合页及其聊天侧栏容器后,相关源断言随之收敛:
    // workspaceSource / chatSlotSource(旧页与 ChatSessionSidebarSlot)已删除。
    const botSidebarSource = readFileSync(
      path.join(root, 'src/pages/Workspace/components/BotSessionSidebar/index.tsx'),
      'utf8',
    );
    const groupFiltersSource = readFileSync(
      path.join(root, 'src/pages/Workspace/components/GroupSidebar/GroupSidebarFilters.tsx'),
      'utf8',
    );
    const actionSource = readFileSync(
      path.join(root, 'src/pages/Workspace/components/WorkspaceActionButton.tsx'),
      'utf8',
    );

    expect(botSidebarSource).not.toContain('WorkspaceActionButton');
    expect(botSidebarSource).not.toContain('onCreateGroup');
    expect(groupFiltersSource).toContain('<WorkspaceActionButton onCreateGroup={onCreateGroup} />');
    expect(actionSource).toContain('label="发起协作"');
    expect(actionSource).not.toContain('添加好友');
    expect(actionSource).not.toContain('Popover');
    expect(existsSync(path.join(root, 'src/pages/Workspace/components/Modals/AddFriendModal.tsx'))).toBe(false);
    expect(existsSync(path.join(root, 'src/pages/Workspace/hooks/usePublicBotCatalog.ts'))).toBe(false);
    expect(existsSync(path.join(root, 'src/pages/Workspace/hooks/useBotCatalogFetch.ts'))).toBe(false);
  });
});
