import type { BotEditorPanelContext, BotEditorPanelExtension } from '@/capabilities';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import { Suspense } from 'react';

type Props = Omit<BotEditorPanelContext, 'ownerId' | 'spaceId'> & {
  ownerId?: string;
  spaceId?: string;
  Panel: BotEditorPanelExtension['Panel'];
};
export function BotEditorExtensionContent({ Panel, ownerId, spaceId, ...context }: Props) {
  if (!ownerId || !spaceId) return <Empty title="缺少配置上下文" description="请从当前空间的 Bot 管理重新进入。" />;
  return (
    <Suspense fallback={<Spin tip="加载中…" />}>
      <Panel
        key={JSON.stringify([context.botId, ownerId, spaceId, context.runtimeStage])}
        {...context}
        ownerId={ownerId}
        spaceId={spaceId}
      />
    </Suspense>
  );
}
