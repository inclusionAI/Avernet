import { BotAccessModal } from '@/components/BotWorkshop/BotAccessModal';
import BotCard from '@/components/BotWorkshop/BotCard';
import BotHealthCheckDrawer from '@/components/BotWorkshop/BotHealthCheckDrawer';
import BotWorkshopToolbar from '@/components/BotWorkshop/BotWorkshopToolbar';
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { ServicePublicationDrawer } from '@/components/BotWorkshop/ServicePublicationDrawer';
import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { Skeleton } from '@/components/ui/Skeleton';
import { useBotWorkshop } from '@/hooks/useBotWorkshop';
import type { BotDomain } from '@/services/botWorkshop';
import React, { useState } from 'react';

const BotWorkshopPage: React.FC = () => {
  const workshop = useBotWorkshop();
  const [publicationBot, setPublicationBot] = useState<BotDomain>();
  return (
    <main className="app-scrollbar h-full overflow-y-auto">
      <div className="w-full space-y-6 p-4 sm:p-6 2xl:p-8">
        <PageHeader title="Bot 工坊" />
        <BotWorkshopToolbar
          keyword={workshop.keyword}
          engine={workshop.engine}
          deployment={workshop.deployment}
          serviceMode={workshop.serviceMode}
          onKeywordChange={workshop.setKeyword}
          onEngineChange={workshop.setEngine}
          onDeploymentChange={workshop.setDeployment}
          onServiceModeChange={workshop.setServiceMode}
          onCreateCloud={workshop.openCreateCloud}
        />
        {workshop.loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:gap-5">
            {[1, 2, 3, 4, 5, 6].map((item) => (
              <Card key={item}>
                <Skeleton.Card />
              </Card>
            ))}
          </div>
        ) : workshop.error ? (
          <Empty
            title="Bot 列表加载失败"
            description={workshop.error}
            action={
              <Button variant="secondary" onClick={() => void workshop.retry()}>
                重试
              </Button>
            }
          />
        ) : workshop.items.length === 0 ? (
          <Empty
            title={workshop.keyword || workshop.engine ? '没有符合条件的 Bot' : '当前空间暂无 Bot'}
            description={
              workshop.keyword || workshop.engine || workshop.deployment || workshop.serviceMode
                ? '尝试清除搜索词或调整筛选条件。'
                : '创建一个 Bot，开始配置它的能力和运行方式。'
            }
            action={<Button onClick={workshop.openCreateCloud}>创建云端 Bot</Button>}
          />
        ) : (
          <>
            <div
              data-testid="bot-workshop-grid"
              className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:gap-5"
            >
              {workshop.items.map((bot) => (
                <BotCard
                  key={bot.entityKey}
                  bot={bot}
                  onView={() => workshop.openDetail(bot)}
                  onConversation={workshop.openConversation}
                  onHealthCheck={workshop.openHealthCheck}
                  healthCheckAvailability={workshop.getHealthCheckAvailability(bot)}
                  onEdit={() => workshop.openDetail(bot, 'edit')}
                  onAction={workshop.runAction}
                  onClaimLock={workshop.claimLock}
                  logAction={workshop.logActionFor(bot)}
                  onOpenLogs={workshop.openLogs}
                  onChangeSpace={workshop.openSpaceChange}
                  onAuthorize={workshop.openAuthorize}
                  collaborationMode={workshop.collaborationModeFor(bot)}
                  onManagePublication={setPublicationBot}
                />
              ))}
            </div>
            {workshop.total !== undefined ? (
              <Pagination
                current={workshop.page}
                pageSize={workshop.pageSize}
                total={workshop.total}
                onChange={workshop.setPage}
                className="justify-end"
              />
            ) : null}
          </>
        )}
        <CreateBotModal
          scenario={workshop.createScenario}
          spaces={workshop.createSpaces}
          creating={workshop.creating}
          authorization={workshop.createAuthorization}
          onClose={workshop.closeCreate}
          onSubmit={workshop.submitCreate}
        />
        <BotAccessModal
          mode={workshop.access.mode}
          bot={workshop.access.bot}
          spaces={workshop.access.spaces}
          loading={workshop.access.loading}
          collaborators={workshop.collaborators}
          onClose={workshop.closeAccess}
          onChangeSpace={workshop.changeSpace}
          onAddCollaborator={workshop.addCollaborator}
          onUpdateCollaborator={workshop.updateCollaborator}
          onRemoveCollaborator={workshop.removeCollaborator}
          onRequestAccess={workshop.requestAccess}
        />
        <BotHealthCheckDrawer
          open={workshop.healthCheck.open}
          botName={workshop.healthCheck.target?.botName}
          summary={workshop.healthCheck.summary}
          loading={workshop.healthCheck.loading}
          checking={workshop.healthCheck.checking}
          error={workshop.healthCheck.error}
          onOpenChange={(open) => {
            if (!open) workshop.healthCheck.closeHealthCheck();
          }}
          onRefresh={workshop.healthCheck.refresh}
          onRunDiagnose={workshop.healthCheck.runDiagnose}
        />
        <ServicePublicationDrawer
          bot={publicationBot}
          onClose={() => setPublicationBot(undefined)}
          onChanged={workshop.retry}
        />
      </div>
    </main>
  );
};

export default BotWorkshopPage;
