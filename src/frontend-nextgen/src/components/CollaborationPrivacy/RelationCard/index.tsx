import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { PendingPublication, PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
import { Info } from 'lucide-react';

const scopeLabels = { none: '不公开', all: '全部公开', restricted: '限制公开范围' } as const;
const audienceLabels: Record<PublicAudience, string> = { user: '其他用户可添加为好友', bot: '其他 Bot 可添加为好友' };
const audienceDescriptions: Record<PublicAudience, string> = {
  user: '控制网络中的其他用户身份，是否可发现并添加此 Bot 为好友。',
  bot: '控制网络中的其他 Bot 身份，是否可发现并添加此 Bot 为好友。',
};

interface RelationCardProps {
  audience: PublicAudience;
  config: PublicConfig;
  pending?: PendingPublication;
  disabled?: boolean;
  onEdit: () => void;
  onViewScope: () => void;
}

export function RelationCard({ audience, config, pending, disabled, onEdit, onViewScope }: RelationCardProps) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <div className="flex items-center gap-1.5">
            <p className="m-0 text-sm font-medium text-foreground">{audienceLabels[audience]}</p>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-5 w-5 text-muted-foreground"
                    aria-label={`${audienceLabels[audience]}说明`}
                  >
                    <Info className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{audienceDescriptions[audience]}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          {pending && <Badge tone="warning">待审批</Badge>}
          {pending?.approvalUrl && (
            <Button asChild variant="ghost" size="sm" className="h-auto p-0 text-primary">
              <a href={pending.approvalUrl} target="_blank" rel="noopener noreferrer">
                查看审批进度
              </a>
            </Button>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          当前生效：{scopeLabels[config.scope]}
          {config.scope === 'restricted' ? ` · ${config.organizationPaths.length} 个组织范围` : ''}
          {config.scope === 'restricted' && (
            <Button
              variant="ghost"
              size="sm"
              className="ml-1 h-auto p-0 align-baseline text-primary"
              onClick={onViewScope}
            >
              查看
            </Button>
          )}
        </p>
      </div>
      <Button variant="secondary" size="sm" disabled={disabled || Boolean(pending)} onClick={onEdit}>
        {pending ? '审批中' : '编辑范围'}
      </Button>
    </div>
  );
}
