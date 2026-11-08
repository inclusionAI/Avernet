import { getCapabilities } from '@/capabilities';
import { Empty } from '@/components/ui';
import type { HumanIdentity } from '@/capabilities';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';

/**
 * Coding Bot 的右侧工作区边界。
 *
 * Open Core 只通过 capability 获取 Stage；未注入时显式 fail-closed，
 * 不导入内部 URL，也不能退回普通聊天 Runtime。
 */
export function CodingBotStageBoundary({
  bot,
  session,
  human,
}: {
  bot: ChatBotView;
  session: BotChatSessionView;
  human: HumanIdentity | null;
}): JSX.Element {
  const host = getCapabilities().getCodingBotConversationHost();
  const Stage = host.value?.Stage;

  if (human === null) {
    return (
      <section className="flex min-h-0 min-w-0 flex-1 items-center justify-center bg-background px-6 py-10">
        <Empty
          className="w-full max-w-xl"
          title="登录身份加载中"
          description="正在确认当前登录人；身份就绪后会自动打开 Coding Bot 对话。"
        />
      </section>
    );
  }

  if (!Stage) {
    return (
      <section className="flex min-h-0 min-w-0 flex-1 items-center justify-center bg-background px-6 py-10">
        <Empty
          className="w-full max-w-xl"
          title="Coding Bot 对话暂不可用"
          description="当前部署未配置 Coding Bot 对话组件，请联系管理员开通后重试。"
        />
      </section>
    );
  }

  // publishId/stage 必须成对传入。当前 ChatBotView 尚未提供服务 Bot 发布元数据,
  // 因此本期不猜测 stage;待后端补齐 publishId 后同处装配。
  return (
    <section className="flex min-h-0 min-w-0 flex-1" data-coding-bot-stage="true">
      <Stage
        botId={bot.realBotId}
        sessionId={session.sessionId}
        ownerId={bot.ownerId}
        userId={human.userId}
        nickName={human.displayName}
        mode="embed"
      />
    </section>
  );
}
