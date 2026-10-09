import { Button } from '@/components';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { ChevronDown, Plus } from 'lucide-react';
import React, { useState } from 'react';
import type { MessageViewScope } from '../types';

interface CreateSessionControlProps {
  isCreating: boolean;
  showScopeMenu: boolean;
  onCreateSession: (messageViewScope?: MessageViewScope) => void;
}

const SESSION_SCOPE_OPTIONS: Array<{
  value: MessageViewScope;
  label: string;
  description: string;
}> = [
  {
    value: 'full',
    label: '完整视角',
    description: '显示本会话内的全部协作消息',
  },
  {
    value: 'participant',
    label: '参与者视角',
    description: '仅显示公共及与您相关的消息',
  },
];

const CreateSessionControl: React.FC<CreateSessionControlProps> = ({
  isCreating,
  showScopeMenu,
  onCreateSession,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);

  if (!showScopeMenu) {
    return (
      <Button
        variant="secondary"
        soft
        size="sm"
        onClick={() => onCreateSession()}
        loading={isCreating}
        leftIcon={<Plus className="h-3.5 w-3.5" />}
        className="h-7 gap-1 text-xs"
      >
        新建会话
      </Button>
    );
  }

  return (
    <div className="inline-flex items-center">
      <Button
        variant="secondary"
        soft
        size="sm"
        onClick={() => onCreateSession()}
        loading={isCreating}
        leftIcon={<Plus className="h-3.5 w-3.5" />}
        className="h-7 gap-1 rounded-r-none pr-2 text-xs"
        title="按群中您的消息视角新建会话"
      >
        新建会话
      </Button>
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="secondary"
            soft
            size="sm"
            disabled={isCreating}
            className="h-7 min-w-0 rounded-l-none border-l border-l-lavender-200 px-1.5"
            aria-label="选择新会话消息视角"
            title="选择消息视角"
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-64 p-1.5">
          <div role="menu" aria-label="新会话消息视角" className="space-y-1">
            {SESSION_SCOPE_OPTIONS.map((option) => (
              <Button
                key={option.value}
                variant="default"
                ghost
                size="sm"
                fullWidth
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  onCreateSession(option.value);
                }}
                className="h-auto items-start justify-start gap-2 rounded-md px-2.5 py-2 text-left hover:bg-lavender-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lavender-400"
              >
                <span className="min-w-0">
                  <span className="block text-xs font-medium text-slate-700">
                    {option.label}
                  </span>
                  <span className="mt-0.5 block text-[10px] leading-relaxed text-slate-400">
                    {option.description}
                  </span>
                </span>
              </Button>
            ))}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default CreateSessionControl;
