import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import {
  BotFriendConversationList,
  BotFriendConversationSidebar,
  type BotFriendConversationSidebarProps,
} from './BotFriendConversationSidebar';

interface BotFriendConversationSidebarSlotProps {
  sidebarProps: BotFriendConversationSidebarProps;
  mobileListOpen: boolean;
  onMobileListClose: () => void;
}

export function BotFriendConversationSidebarSlot({
  sidebarProps,
  mobileListOpen,
  onMobileListClose,
}: BotFriendConversationSidebarSlotProps) {
  return (
    <>
      <BotFriendConversationSidebar {...sidebarProps} />
      <Drawer
        open={mobileListOpen}
        onOpenChange={(open) => {
          if (!open) onMobileListClose();
        }}
      >
        <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">好友会话列表</DrawerTitle>
          <BotFriendConversationList
            {...sidebarProps}
            onSelectSession={(sessionId) => {
              sidebarProps.onSelectSession(sessionId);
              onMobileListClose();
            }}
          />
        </DrawerContent>
      </Drawer>
    </>
  );
}
