import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import { GroupSidebar, GroupSidebarList, type GroupSidebarProps } from './GroupSidebar';

/** 桌面与移动端共享范围约束；session-only 页不挂载本组件。 */
export function GroupWorkspaceSidebar({
  sidebar,
  open,
  onClose,
}: {
  sidebar: GroupSidebarProps;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <>
      <GroupSidebar {...sidebar} />
      <Drawer
        open={open}
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
      >
        <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">协作群列表</DrawerTitle>
          <GroupSidebarList
            {...sidebar}
            onSelectSession={(gid, id) => {
              sidebar.onSelectSession(gid, id);
              onClose();
            }}
          />
        </DrawerContent>
      </Drawer>
    </>
  );
}
