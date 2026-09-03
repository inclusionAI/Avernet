import { getCapabilities } from '@/capabilities';
import { BotAccessModal } from '@/components/BotWorkshop/BotAccessModal';
import BotCard from '@/components/BotWorkshop/BotCard';
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
  const showBotLogs = getCapabilities().getLoginStrategy().value === 'ace-gateway';
  return (
    <main className="app-scrollbar h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[1600px] space-y-5 p-4 sm:p-6 2xl:p-8">
        <PageHeader title="Bot 工坊" description="创建、配置和运维当前空间内的 Bot。" />
        <div className="border-y border-border bg-background py-3">
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
            total={workshop.total}
            onReset={() => {
              workshop.setKeyword('');
              workshop.setEngine('');
              workshop.setDeployment(undefined);
              workshop.setServiceMode(undefined);
            }}
          />
        </div>
        {workshop.loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
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
            <div data-testid="bot-workshop-grid" className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {workshop.items.map((bot) => (
                <BotCard
                  key={bot.cardId ?? bot.entityKey}
                  bot={bot}
                  onView={() => workshop.openDetail(bot)}
                  onConversation={workshop.openConversation}
                  onHealthCheck={workshop.openHealthCheck}
                  healthCheckAvailability={workshop.getHealthCheckAvailability(bot)}
                  onEdit={() => workshop.openDetail(bot, 'edit')}
                  onAction={workshop.runAction}
                  onClaimLock={workshop.claimLock}
                  logAction={showBotLogs ? workshop.logActionFor(bot) : undefined}
                  onOpenLogs={showBotLogs ? workshop.openLogs : undefined}
                  onChangeSpace={workshop.canChangeSpace(bot) ? workshop.openSpaceChange : undefined}
                  onAuthorize={workshop.collaborationModeFor(bot) ? workshop.openAuthorize : undefined}
                  collaborationMode={workshop.collaborationModeFor(bot)}
                  onManagePublication={setPublicationBot}
                  inventoryActions={{
                    view: workshop.inventoryActionFor(bot, 'view'),
                    chat: workshop.inventoryActionFor(bot, 'chat'),
                    edit: workshop.inventoryActionFor(bot, 'edit'),
                  }}
                />
              ))}
            </div>
            {workshop.total !== undefined ? (
              <Pagination
                current={workshop.page}
                pageSize={workshop.pageSize}
                total={workshop.total}
                onChange={workshop.setPage}
                onPageSizeChange={workshop.setPageSize}
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
          agentCodingTemplates={workshop.agentCodingTemplates}
          agentCodingTemplatesLoading={workshop.agentCodingTemplatesLoading}
          agentCodingTemplatesError={workshop.agentCodingTemplatesError}
          onRetryAgentCodingTemplates={workshop.retryAgentCodingTemplates}
        />
        <BotAccessModal
          mode={workshop.access.mode}
          bot={workshop.access.bot}
          spaces={workshop.access.spaces}
          loading={workshop.access.loading}
          operation={workshop.access.operation}
          collaborators={workshop.collaborators}
          onClose={workshop.closeAccess}
          onChangeSpace={workshop.changeSpace}
          onCreateTeamAndChangeSpace={workshop.createTeamAndChangeSpace}
          onAddCollaborator={workshop.addCollaborator}
          onUpdateCollaborator={workshop.updateCollaborator}
          onRemoveCollaborator={workshop.removeCollaborator}
          onRequestAccess={workshop.requestAccess}
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
