import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import { useEffect, useRef } from 'react';
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
  const wasOpenRef = useRef(open);
  useEffect(() => {
    // 响应式父级也会直接关闭受控抽屉，此路径不会触发 Radix onOpenChange。
    if (wasOpenRef.current && !open && sidebar.location) sidebar.onDismissLocation?.();
    wasOpenRef.current = open;
  }, [open, sidebar.location, sidebar.onDismissLocation]);
  const closeList = () => {
    sidebar.onDismissLocation?.();
    onClose();
  };
  return (
    <>
      <GroupSidebar {...sidebar} />
      <Drawer
        open={open}
        onOpenChange={(next) => {
          if (!next) closeList();
        }}
      >
        <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">协作群列表</DrawerTitle>
          <GroupSidebarList
            {...sidebar}
            onSelectSession={(gid, id) => {
              sidebar.onSelectSession(gid, id);
              closeList();
            }}
            onManageSession={(gid, id) => {
              sidebar.onManageSession(gid, id);
              closeList();
            }}
            onManageGroup={(gid) => {
              sidebar.onManageGroup(gid);
              closeList();
            }}
          />
        </DrawerContent>
      </Drawer>
    </>
  );
}
