import { PageHeader } from '@/components/Common/PageHeader';
import { ExternalBotPanel } from '@/components/ExternalBots/ExternalBotPanel';

/**
 * collab 形态的外部 Bot 管理页（/external-bots）：复用 Bot 管理「外部 Bot」面板，
 * 不经 BotWorkshop 页壳（避免拖入 useBotWorkshop 全栈）。数据全部来自 collaboration 接口。
 */
export default function ExternalBotsPage(): JSX.Element {
  return (
    <main className="app-scrollbar h-full overflow-y-auto bg-page-canvas">
      <div className="w-full p-5 sm:p-6">
        <PageHeader title="外部 Bot" description="接入、查看和配置当前空间内的外部 Bot" />
        <section className="mt-5 rounded-xl border border-border/60 bg-card p-4 sm:p-6">
          <ExternalBotPanel active />
        </section>
      </div>
    </main>
  );
}
