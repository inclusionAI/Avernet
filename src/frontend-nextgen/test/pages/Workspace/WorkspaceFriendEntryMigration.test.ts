import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

describe('Workspace 好友入口迁移', () => {
  it('任务执行受限时保留跳转协作权限的恢复入口', () => {
    const workspaceSource = readFileSync(path.join(process.cwd(), 'src/pages/Workspace/index.tsx'), 'utf8');

    expect(workspaceSource).toContain("navigate('/collaboration-privacy')");
    expect(workspaceSource).toContain('onOpenCollaborationPermissions: openCollaborationPermissions');
  });

  it('移除添加好友弹窗和重复目录状态机，只在协作群工具行保留发起协作', () => {
    const root = process.cwd();
    const workspaceSource = readFileSync(path.join(root, 'src/pages/Workspace/index.tsx'), 'utf8');
    const chatSlotSource = readFileSync(
      path.join(root, 'src/pages/Workspace/components/ChatSessionSidebarSlot.tsx'),
      'utf8',
    );
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

    expect(workspaceSource).not.toContain('AddFriendModal');
    expect(workspaceSource).not.toContain('addFriendOpen');
    expect(workspaceSource).not.toContain('onOpenAddFriend');
    expect(workspaceSource).not.toContain('createGroupOpen');
    expect(workspaceSource).not.toContain('CreateGroupModal');
    expect(chatSlotSource).not.toContain('onOpenCreateGroup');
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
