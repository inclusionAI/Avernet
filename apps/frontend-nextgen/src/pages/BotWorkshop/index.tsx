import { getCapabilities } from '@/capabilities';
import { BotAccessModal } from '@/components/BotWorkshop/BotAccessModal';
import { BotBasicInfoDrawer } from '@/components/BotWorkshop/BotBasicInfoDrawer';
import BotTable from '@/components/BotWorkshop/BotCard';
import BotTableSkeleton from '@/components/BotWorkshop/BotCard/BotTableSkeleton';
import { BotManagementTabs, type BotManagementTab } from '@/components/BotWorkshop/BotManagementTabs';
import BotWorkshopToolbar from '@/components/BotWorkshop/BotWorkshopToolbar';
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { ExternalBotPanel } from '@/components/BotWorkshop/ExternalBotPanel';
import { ServicePublicationDrawer } from '@/components/BotWorkshop/ServicePublicationDrawer';
import { BotGeneralConfigDialog } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog';
import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { useBotWorkshop } from '@/hooks/useBotWorkshop';
import { getGeneralConfigAvailability, type BotDomain } from '@/services/botWorkshop';
import React, { useState } from 'react';

const BotWorkshopPage: React.FC = () => {
  const workshop = useBotWorkshop();
  const [publicationBot, setPublicationBot] = useState<BotDomain>();
  const [generalConfigBot, setGeneralConfigBot] = useState<BotDomain>();
  const [basicInfoBot, setBasicInfoBot] = useState<BotDomain>();
  const [activeTab, setActiveTab] = useState<BotManagementTab>('tc');
  const showBotLogs = getCapabilities().getLoginStrategy().value === 'ace-gateway';
  const effectiveTab = workshop.currentSpaceKind === 'personal' ? activeTab : 'tc';
  const handleView = (bot: BotDomain) => {
    if (workshop.canEnterDetail(bot)) workshop.openDetail(bot);
    else setBasicInfoBot(bot);
  };
  return (
    <main className="app-scrollbar h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[1600px] space-y-5 p-4 sm:p-6 2xl:p-8">
        <PageHeader title="Bot 管理" description="创建、接入、配置和运维当前空间内的 Bot" />
        {workshop.currentSpaceKind === 'personal' ? (
          <BotManagementTabs value={activeTab} onChange={setActiveTab} />
        ) : null}
        {effectiveTab === 'external' ? (
          <ExternalBotPanel active />
        ) : (
          <>
            <div className="py-3">
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
                onCreateLocal={workshop.openCreateLocal}
                localCreateDisabledReason={workshop.localCreateDisabledReason}
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
              <BotTableSkeleton showOwner={workshop.currentSpaceKind === 'team'} />
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
                <div data-testid="bot-workshop-table">
                  <BotTable
                    bots={workshop.items}
                    onView={handleView}
                    onEdit={(bot) =>
                      workshop.canEnterDetail(bot) ? workshop.openDetail(bot, 'edit') : setBasicInfoBot(bot)
                    }
                    canEnterDetail={workshop.canEnterDetail}
                    onConversation={workshop.openConversation}
                    canOpenConversation={workshop.canOpenConversation}
                    onHealthCheck={workshop.openHealthCheck}
                    getHealthCheckAvailability={workshop.getHealthCheckAvailability}
                    onOpenLogs={showBotLogs ? workshop.openLogs : undefined}
                    getLogAction={showBotLogs ? workshop.logActionFor : undefined}
                    onChangeSpace={(bot) => {
                      if (workshop.canChangeSpace(bot)) workshop.openSpaceChange(bot);
                    }}
                    onAuthorize={workshop.openAuthorize}
                    getCollaborationMode={workshop.collaborationModeFor}
                    getGeneralConfigAvailability={(bot) =>
                      getGeneralConfigAvailability(workshop.canChangeSpace(bot), bot.lock?.status === 'other')
                    }
                    onGeneralConfig={setGeneralConfigBot}
                    onManagePublication={setPublicationBot}
                    onAction={workshop.runAction}
                    onClaimLock={workshop.claimLock}
                    onReleaseLock={workshop.releaseLock}
                    getInventoryActions={(bot) => ({
                      view: workshop.inventoryActionFor(bot, 'view'),
                      chat: workshop.inventoryActionFor(bot, 'chat'),
                      edit: workshop.inventoryActionFor(bot, 'edit'),
                    })}
                  />
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
          operation={workshop.access.operation}
          collaborators={workshop.collaborators}
          members={workshop.access.members}
          autoApproveEditorRequests={workshop.access.autoApproveEditorRequests}
          policyError={workshop.access.policyError}
          onClose={workshop.closeAccess}
          onChangeSpace={workshop.changeSpace}
          onCreateTeamAndChangeSpace={workshop.createTeamAndChangeSpace}
          onAddCollaborator={workshop.addCollaborator}
          onUpdateCollaborator={workshop.updateCollaborator}
          onRemoveCollaborator={workshop.removeCollaborator}
          onEditorRequestPolicyChange={workshop.updateEditorRequestPolicy}
          onRequestAccess={workshop.requestAccess}
        />
        <ServicePublicationDrawer
          bot={publicationBot}
          onClose={() => {
            setPublicationBot(undefined);
            void workshop.retry({ silent: true });
          }}
          onChanged={workshop.retry}
        />
        {generalConfigBot ? (
          <BotGeneralConfigDialog bot={generalConfigBot} onClose={() => setGeneralConfigBot(undefined)} />
        ) : null}
        <BotBasicInfoDrawer bot={basicInfoBot} onClose={() => setBasicInfoBot(undefined)} />
      </div>
    </main>
  );
};

export default BotWorkshopPage;
