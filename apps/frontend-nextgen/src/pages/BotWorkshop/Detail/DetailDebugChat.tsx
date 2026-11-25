import { DebugChatPanel } from '@/components/BotWorkshop/Editor/DebugChatPanel';
import { cn } from '@/utils/cn';
import type { ComponentProps } from 'react';

/** 调试区的响应式外框；请求身份由编辑页明确传入。 */
export function DetailDebugChat(props: ComponentProps<typeof DebugChatPanel>) {
  return (
    <div
      className={cn(
        'hidden min-w-0 flex-1 transition-[flex-basis,width] duration-200 ease-out lg:flex',
        props.fullscreen && 'flex',
      )}
    >
      <DebugChatPanel {...props} />
    </div>
  );
}
