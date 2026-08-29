import { Badge } from '@/components/ui/Badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';
export interface BotPublishPanelProps {
  bot?: BotDomain;
}
const BotPublishPanel: React.FC<BotPublishPanelProps> = ({ bot }) => (
  <Card>
    <CardHeader>
      <CardTitle>发布与运行状态</CardTitle>
      <Badge tone={bot?.runtime.capabilityProfile.canPublish ? 'success' : 'neutral'}>
        {bot?.runtime.capabilityProfile.canPublish ? '支持发布' : '暂不支持'}
      </Badge>
    </CardHeader>
    <CardContent>
      <p className="m-0 text-sm text-[var(--color-muted)]">
        {bot ? '发布、下线和版本管理将在接口契约确认后开放。' : '请选择一个 Bot 查看发布配置。'}
      </p>
      <p className="mt-2 text-xs text-[var(--color-warning)]">TODO：发布接口、审批协议和并发锁尚未接入。</p>
    </CardContent>
  </Card>
);
export default BotPublishPanel;
