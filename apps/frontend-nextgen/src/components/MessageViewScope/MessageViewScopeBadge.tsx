import { Badge } from '@/components/ui';
import { MESSAGE_VIEW_SCOPE_LABEL } from '@/domain/collaboration/messageViewScope';
import type { MessageViewScope } from '@/domain/collaboration/types';

/** 消息可见域标签：full 主色蓝（primary），participant 灰（neutral）。 */
export function MessageViewScopeBadge({ scope, className }: { scope: MessageViewScope; className?: string }) {
  return (
    <Badge className={className} tone={scope === 'full' ? 'primary' : 'neutral'}>
      {MESSAGE_VIEW_SCOPE_LABEL[scope]}
    </Badge>
  );
}
