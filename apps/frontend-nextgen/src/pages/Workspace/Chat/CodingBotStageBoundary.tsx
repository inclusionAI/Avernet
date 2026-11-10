import type { HumanIdentity } from '@/capabilities';
import { getCapabilities } from '@/capabilities';
import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import { Button, Empty } from '@/components/ui';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import {
  resolveServiceBotPublishSelection,
  type ServiceBotPublishSelection,
} from '@/services/workspace/serviceBotPublishResolver';
import { useCallback, useEffect, useRef, useState } from 'react';

type ServicePublishResolution =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'ready'; selection: ServiceBotPublishSelection }
  | { phase: 'error'; error: string };

const IDLE_PUBLISH_RESOLUTION: ServicePublishResolution = { phase: 'idle' };

function useOnlineServicePublishResolution(bot: ChatBotView, enabled: boolean) {
  const [resolution, setResolution] = useState<ServicePublishResolution>(IDLE_PUBLISH_RESOLUTION);
  const requestRef = useRef(0);
  const [retryNonce, setRetryNonce] = useState(0);

  const retry = useCallback(() => setRetryNonce((current) => current + 1), []);
  const required =
    enabled && bot.botType === 'service' && bot.runtimeStage === 'online';

  useEffect(() => {
    if (!required) {
      requestRef.current += 1;
      setResolution(IDLE_PUBLISH_RESOLUTION);
      return;
    }

    const seq = requestRef.current + 1;
    requestRef.current = seq;
    setResolution({ phase: 'loading' });
    void resolveServiceBotPublishSelection({
      botId: bot.realBotId,
      ownerId: bot.ownerId,
      stage: 'online',
    })
      .then((selection) => {
        if (requestRef.current === seq) setResolution({ phase: 'ready', selection });
      })
      .catch((error: unknown) => {
        if (requestRef.current === seq) {
          setResolution({
            phase: 'error',
            error: error instanceof Error ? error.message : '解析线上服务发布信息失败',
          });
        }
      });
  }, [bot.botType, bot.ownerId, bot.realBotId, bot.runtimeStage, required, retryNonce]);

  return { resolution, retry };
}

export function CodingBotStageBoundary({
  bot,
  session,
  human,
}: {
  bot: ChatBotView;
  session?: BotChatSessionView | null;
  human: HumanIdentity | null;
}): JSX.Element {
  const host = getCapabilities().getCodingBotConversationHost();
  const Stage = host.value?.Stage;
  const identityReady = human !== null;
  const hostReady = Stage !== undefined;
  const { resolution, retry } = useOnlineServicePublishResolution(bot, hostReady && identityReady);
  const requiresServicePublish = bot.botType === 'service' && bot.runtimeStage === 'online';

  const renderBoundary = (title: string, description: string) => (
    <section className="flex min-h-0 min-w-0 flex-1 items-center justify-center bg-background px-6 py-10">
      <Empty className="w-full max-w-xl" title={title} description={description} />
    </section>
  );

  const renderBootSkeleton = () => (
    <section className="h-full min-h-0 w-full overflow-hidden bg-background">
      <ChatMessageSkeleton ariaLabel="Coding Bot 对话加载中" />
    </section>
  );

  if (human === null) {
    return renderBoundary('登录身份加载中', '正在确认当前登录人；身份就绪后会自动打开 Coding Bot 对话。');
  }

  if (!Stage) {
    return renderBoundary('Coding Bot 对话暂不可用', '当前部署未配置 Coding Bot 对话组件，请联系管理员开通后重试。');
  }

  if (resolution.phase === 'loading' || (requiresServicePublish && resolution.phase === 'idle')) {
    return renderBootSkeleton();
  }

  if (resolution.phase === 'error') {
    return (
      <section className="flex min-h-0 min-w-0 flex-1 items-center justify-center bg-background px-6 py-10">
        <Empty
          className="w-full max-w-xl"
          title="服务运行环境解析失败"
          description={resolution.error || '无法解析当前线上服务 Coding Bot 的发布信息。'}
          action={<Button onClick={retry}>重新加载</Button>}
        />
      </section>
    );
  }

  const publishSelectionProps =
    resolution.phase === 'ready' ? { ...resolution.selection } : {};

  return (
    <section className="flex min-h-0 min-w-0 flex-1" data-coding-bot-stage="true">
      <Stage
        conversationBotId={bot.botId}
        botId={bot.realBotId}
        sessionId={session?.sessionId}
        ownerId={bot.ownerId}
        userId={human.userId}
        nickName={human.displayName}
        {...publishSelectionProps}
        mode="embed"
      />
    </section>
  );
}
