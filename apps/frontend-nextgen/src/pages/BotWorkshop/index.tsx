import { getCapabilities } from '@/capabilities';
import { BotAccessModal } from '@/components/BotWorkshop/BotAccessModal';
import { BotBasicInfoDrawer } from '@/components/BotWorkshop/BotBasicInfoDrawer';
import BotTable from '@/components/BotWorkshop/BotCard';
import BotTableSkeleton from '@/components/BotWorkshop/BotCard/BotTableSkeleton';
import { BotManagementTabs, type BotManagementTab } from '@/components/BotWorkshop/BotManagementTabs';
import BotWorkshopToolbar from '@/components/BotWorkshop/BotWorkshopToolbar';
import { ContainerInstancesModal } from '@/components/BotWorkshop/ContainerInstancesModal';
import CopyBotModal from '@/components/BotWorkshop/CopyBotModal';
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { ExternalBotPanel } from '@/components/BotWorkshop/ExternalBotPanel';
import { ServicePublicationDrawer } from '@/components/BotWorkshop/ServicePublicationDrawer';
import { BotGeneralConfigDialog } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog';
import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { useBotWorkshop } from '@/hooks/useBotWorkshop';
import { getGeneralConfigAvailability, type BotDomain } from '@/services/botWorkshop';
import { serviceBotEvalService } from '@/services/botWorkshop/serviceBotEvalService';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { history } from '@umijs/max';
import React, { useEffect, useState } from 'react';

const BotWorkshopPage: React.FC = () => {
  const workshop = useBotWorkshop();
  // 对话菜单落地参数(2026-11-05):?create=cloud 默认唤起云端创建;?keyword=Bot 名称按名称
  // 过滤定位该 Bot(Bot 行「跳转至 Bot 管理」前置入口传入)。挂载一次性消费并清参,避免刷新/回退重复触发。
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const keyword = params.get('keyword');
    const createCloud = params.get('create') === 'cloud';
    if (keyword) workshop.setKeyword(keyword);
    if (createCloud) workshop.openCreateCloud();
    if (keyword || createCloud) history.replace('/bot-workshop');
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载一次性消费,workshop 方法每渲染重建不进依赖。
  }, []);
  const [publicationBot, setPublicationBot] = useState<BotDomain>();
  const [evalGateModalBot, setEvalGateModalBot] = useState<BotDomain>();
  const [generalConfigBot, setGeneralConfigBot] = useState<BotDomain>();
  const [basicInfoBot, setBasicInfoBot] = useState<BotDomain>();
  const [containerBot, setContainerBot] = useState<BotDomain>();
  const [activeTab, setActiveTab] = useState<BotManagementTab>('tc');
  const showBotLogs = getCapabilities().getLoginStrategy().value === 'ace-gateway';
  const effectiveTab = workshop.currentSpaceKind === 'personal' ? activeTab : 'tc';
  // 复制 Modal 第 2 步只读展示源 Bot 归属空间名;不复制时不渲染。
  const copySourceSpaces = workshop.copyBot
    ? [
        {
          id: workshop.copyBot.spaceId ?? '',
          name: workshop.copyBot.spaceName ?? workshop.copyBot.spaceId ?? '当前空间',
          ownership: workshop.copyBot.ownership,
          canCreate: false,
        },
      ]
    : [];
  const handleView = (bot: BotDomain) => {
    Tracert.click(Points.botWorkshop.openDetail, { bot_id: bot.id });
    if (workshop.canEnterDetail(bot)) workshop.openDetail(bot);
    else setBasicInfoBot(bot);
  };
  const openEvaluation = (bot: BotDomain, tab?: 'simple_cert_submit' | 'eval_history') => {
    const version = bot.publicationVersion ?? bot.liveVersion ?? 'latest';
    const params = new URLSearchParams();
    if (bot.ownerId) params.set('owner_id', bot.ownerId);
    if (tab) params.set('tab', tab);
    const query = params.toString();
    history.push(
      `/bot-workshop/evaluation/${encodeURIComponent(bot.id)}/versions/${encodeURIComponent(String(version))}` +
        (query ? `?${query}` : ''),
    );
  };
  const openEvaluationHistory = (bot: BotDomain) => openEvaluation(bot, 'eval_history');
  const handleManagePublication = async (bot: BotDomain) => {
    if (bot.lifecycle !== 'draft') {
      setPublicationBot(bot);
      return;
    }
    try {
      const version = String(bot.publicationVersion ?? bot.liveVersion ?? '');
      if (!version) throw new Error('评测版本不存在，无法推进发布');
      const gate = await serviceBotEvalService.precheck(bot.id, version, bot.ownerId);
      if (gate.allowed) {
        setPublicationBot(bot);
        return;
      }
      setEvalGateModalBot(bot);
    } catch (error) {
      if (error instanceof Error && error.message === '评测版本不存在，无法推进发布') setEvalGateModalBot(bot);
      else setEvalGateModalBot(bot);
    }
  };
  return (
    <main className="app-scrollbar h-full overflow-y-auto bg-page-canvas">
      <div className="w-full p-5 sm:p-6">
        <PageHeader title="Bot 管理" description="创建、接入、配置和运维当前空间内的 Bot" />
        {/*
          dmore 稿（10/19 Bot管理首页）：灰底画布（#F8F8F8 = bg-page-canvas）+ 单张白色 r12 卡片，
          卡内自上而下为 Tabs → 筛选行 → 结果面板 → 分页。
          验收（bot-list 复核）：白色卡片不描边，靠白/灰底色差自明；页面内已无 border-border/60。
        */}
        <section className="mt-5 rounded-xl bg-card p-4 sm:p-6">
          {workshop.currentSpaceKind === 'personal' ? (
            <BotManagementTabs value={activeTab} onChange={setActiveTab} className="mb-4" />
          ) : null}
          {effectiveTab === 'external' ? (
            <ExternalBotPanel active />
          ) : (
            <>
              <div className="mb-4">
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
                  onReset={() => {
                    workshop.setKeyword('');
                    workshop.setEngine('');
                    workshop.setDeployment(undefined);
                    workshop.setServiceMode(undefined);
                  }}
                />
              </div>
              {/* 结果面板：数据视图为白底 + 灰蓝表头条（白底上不再描边，靠表头/行分割线自明）；
                  空态/错误态为 2% 灰底的居中留白面板。 */}
              {workshop.loading ? (
                <div className="overflow-hidden rounded-[10px] bg-card">
                  <BotTableSkeleton showOwner={workshop.currentSpaceKind === 'team'} />
                </div>
              ) : workshop.error ? (
                <div className="rounded-[10px] bg-muted/40">
                  <Empty
                    title="Bot 列表加载失败"
                    description={workshop.error}
                    action={
                      <Button variant="secondary" onClick={() => void workshop.retry()}>
                        重试
                      </Button>
                    }
                  />
                </div>
              ) : workshop.items.length === 0 ? (
                <div className="rounded-[10px] bg-muted/40">
                  <Empty
                    title={workshop.keyword || workshop.engine ? '没有符合条件的 Bot' : '当前空间暂无 Bot'}
                    description={
                      workshop.keyword || workshop.engine || workshop.deployment || workshop.serviceMode
                        ? '尝试清除搜索词或调整筛选条件。'
                        : '创建一个 Bot，开始配置它的能力和运行方式。'
                    }
                    action={<Button onClick={workshop.openCreateCloud}>创建云端 Bot</Button>}
                  />
                </div>
              ) : (
                <>
                  <div data-testid="bot-workshop-table" className="overflow-hidden rounded-[10px] bg-card">
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
                      onOpenTaskInstances={workshop.openTaskInstances}
                      canOpenTaskInstances={workshop.canOpenTaskInstances}
                      onOpenContainers={setContainerBot}
                      canViewContainers={workshop.canViewContainers}
                      containerUserId={workshop.containerUserId}
                      onEngineVersionUpdated={() => workshop.retry({ silent: true })}
                      onChangeSpace={(bot) => {
                        if (workshop.canChangeSpace(bot)) workshop.openSpaceChange(bot);
                      }}
                      onAuthorize={workshop.openAuthorize}
                      getCollaborationMode={workshop.collaborationModeFor}
                      getGeneralConfigAvailability={(bot) =>
                        getGeneralConfigAvailability(bot, workshop.canChangeSpace(bot), bot.lock?.status === 'other')
                      }
                      onGeneralConfig={setGeneralConfigBot}
                      onManagePublication={(bot) => void handleManagePublication(bot)}
                      onOpenEvaluation={openEvaluation}
                      onOpenEvalHistory={openEvaluationHistory}
                      onAction={workshop.runAction}
                      onCopy={workshop.openCopy}
                      getCopyAvailability={workshop.getCopyAvailability}
                      onRetryCopy={(bot) => void workshop.retryCopy(bot.id)}
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
                      layout="between"
                      className="mt-4"
                    />
                  ) : null}
                </>
              )}
            </>
          )}
        </section>
        <CreateBotModal
          scenario={workshop.createScenario}
          spaces={workshop.createSpaces}
          creating={workshop.creating}
          authorization={workshop.createAuthorization}
          onClose={workshop.closeCreate}
          onSubmit={workshop.submitCreate}
          onExpertCreated={() => void workshop.retry()}
        />
        <CopyBotModal
          sourceBot={workshop.copyBot}
          spaces={copySourceSpaces}
          copying={workshop.copying}
          authorization={workshop.copyAuthorization}
          onClose={workshop.closeCopy}
          onSubmit={workshop.submitCopy}
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
        <Modal
          open={Boolean(evalGateModalBot)}
          onOpenChange={(open) => {
            if (!open) setEvalGateModalBot(undefined);
          }}
        >
          <ModalContent size="sm" showClose={false}>
            <ModalHeader>
              <ModalTitle>无法推进发布</ModalTitle>
              <ModalDescription>评测未通过，无法推进发布</ModalDescription>
            </ModalHeader>
            <p className="m-0 text-sm leading-6 text-muted-foreground">
              请完成评测数据上报认证，或重新发起评测后再推进发布。
            </p>
            <ModalFooter>
              <Button variant="secondary" onClick={() => setEvalGateModalBot(undefined)}>
                稍后处理
              </Button>
              <Button
                onClick={() => {
                  if (evalGateModalBot) openEvaluation(evalGateModalBot);
                  setEvalGateModalBot(undefined);
                }}
              >
                去评测认证
              </Button>
            </ModalFooter>
          </ModalContent>
        </Modal>
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
        <ContainerInstancesModal
          bot={containerBot}
          userId={workshop.containerUserId}
          canRestart={containerBot ? workshop.canRestartContainer(containerBot) : false}
          onClose={() => setContainerBot(undefined)}
        />
      </div>
    </main>
  );
};

export default BotWorkshopPage;
