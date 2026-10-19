// <lg 二级会话列表抽屉:与桌面端共用同一 Content,选中后由页面收起。
import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import {
  ConversationSidebarContent,
  type ConversationSidebarProps,
} from './ConversationSidebar';

interface ConversationMobileSidebarDrawerProps {
  sidebarProps: ConversationSidebarProps;
  open: boolean;
  onOpenChange(open: boolean): void;
  onOpenSession: ConversationSidebarProps['onOpenSession'];
}

export function ConversationMobileSidebarDrawer({
  sidebarProps,
  open,
  onOpenChange,
  onOpenSession,
}: ConversationMobileSidebarDrawerProps): JSX.Element {
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
        <DrawerTitle className="sr-only">会话列表</DrawerTitle>
        <ConversationSidebarContent {...sidebarProps} onOpenSession={onOpenSession} />
      </DrawerContent>
    </Drawer>
  );
}
