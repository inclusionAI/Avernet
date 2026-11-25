import { GroupContextDeliveryField } from '@/components/GroupContextDeliveryField';
import { MessageViewScopeField } from '@/components/MessageViewScope';
import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { DEFAULT_MESSAGE_VIEW_SCOPE } from '@/domain/collaboration/messageViewScope';
import type { GroupContextDelivery, MessageViewScope } from '@/domain/collaboration/types';
import { Plus } from 'lucide-react';
import { useState } from 'react';

interface CreateSessionMenuProps {
  viewerKind: 'user' | 'bot';
  onCreate: (scope: MessageViewScope | undefined, contextDelivery: GroupContextDelivery) => void;
}

export function CreateSessionMenu({ viewerKind, onCreate }: CreateSessionMenuProps) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<MessageViewScope>(DEFAULT_MESSAGE_VIEW_SCOPE);
  const [contextDelivery, setContextDelivery] = useState<GroupContextDelivery>('send');

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setScope(DEFAULT_MESSAGE_VIEW_SCOPE);
          setContextDelivery('send');
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <IconButton
          label="新建会话"
          size="sm"
          icon={<Plus className="h-3.5 w-3.5" />}
          className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={(event) => event.stopPropagation()}
        />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 space-y-4 p-3">
        <GroupContextDeliveryField value={contextDelivery} onChange={setContextDelivery} />
        {viewerKind === 'user' && <MessageViewScopeField value={scope} onChange={setScope} />}
        <Button
          className="w-full"
          size="sm"
          onClick={() => {
            setOpen(false);
            onCreate(viewerKind === 'user' ? scope : undefined, contextDelivery);
          }}
        >
          创建会话
        </Button>
      </PopoverContent>
    </Popover>
  );
}
