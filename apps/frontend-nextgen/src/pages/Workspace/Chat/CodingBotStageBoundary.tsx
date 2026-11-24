import type { CodingBotConversationHostProps, HumanIdentity } from '@/capabilities';
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
  const key = JSON.stringify([bot.realBotId, bot.ownerId, bot.runtimeStage, enabled]);
  const [resolvedKey, setResolvedKey] = useState(key);
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
    setResolvedKey(key);
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
            error: error instanceof Error ? error.message : '解析正式运行的服务发布信息失败',
          });
        }
      });
    return () => { requestRef.current += 1; };
  }, [key, bot.botType, bot.ownerId, bot.realBotId, bot.runtimeStage, required, retryNonce]);

  return { resolution: resolvedKey === key ? resolution : IDLE_PUBLISH_RESOLUTION, retry };
}

export function CodingBotStageBoundary({
  workspaceActive = true,
  bot,
  session,
  human,
}: {
  workspaceActive?: boolean;
  bot: ChatBotView;
  session?: BotChatSessionView | null;
  human: HumanIdentity | null;
}): JSX.Element {
  const host = getCapabilities().getCodingBotConversationHost();
  const Stage = host.value?.Stage;
  const identityReady = human !== null;
  const hostReady = Stage !== undefined;
  const { resolution, retry } = useOnlineServicePublishResolution(bot, hostReady && identityReady && workspaceActive);
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

  const ready = workspaceActive && identityReady && hostReady &&
    (!requiresServicePublish || resolution.phase === 'ready');
  const nextProps: CodingBotConversationHostProps | null = ready && human ? {
    conversationBotId: bot.botId, botId: bot.realBotId, sessionId: session?.sessionId,
    ownerId: bot.ownerId, userId: human.userId, nickName: human.displayName,
    ...(requiresServicePublish && resolution.phase === 'ready' ? resolution.selection : {}),
    mode: 'embed',
  } : null;
  const nextKey = nextProps ? JSON.stringify(nextProps) : null;
  const [retained, setRetained] = useState<{ key: string; props: CodingBotConversationHostProps } | null>(null);
  if (nextProps && nextKey && nextKey !== retained?.key) setRetained({ key: nextKey, props: nextProps });
  const stageProps = nextProps ?? retained?.props;
  const canRetain = host.value?.supportsWorkspaceSuspension === true;
  let notice = null;
  if (human === null) {
    notice = renderBoundary('登录身份加载中', '正在确认当前登录人；身份就绪后会自动打开 Coding Bot 对话。');
  } else if (!Stage) {
    notice = renderBoundary('Coding Bot 对话暂不可用', '当前部署未配置 Coding Bot 对话组件，请联系管理员开通后重试。');
  } else if (requiresServicePublish && resolution.phase === 'error') {
    notice = (
      <section className="flex min-h-0 min-w-0 flex-1 items-center justify-center bg-background px-6 py-10">
        <Empty className="w-full max-w-xl" title="服务运行环境解析失败" description={resolution.error}
          action={<Button onClick={retry}>重新加载</Button>} />
      </section>
    );
  } else if (!ready && workspaceActive) {
    notice = renderBootSkeleton();
  }
  return (
    <>
      {Stage && human && stageProps && stageProps.userId === human.userId && (ready || canRetain) && (
        <section className="flex min-h-0 min-w-0 flex-1" data-coding-bot-stage="true"
          style={ready ? undefined : { display: 'none' }} aria-hidden={!ready}>
          <Stage {...stageProps} workspaceActive={ready} />
        </section>
      )}
      {workspaceActive ? notice : null}
    </>
  );
}
