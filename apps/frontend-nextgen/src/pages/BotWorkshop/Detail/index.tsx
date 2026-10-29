import { useBotEditorAddress } from './useBotEditorAddress';
import { BotEditorExtensionContent } from './BotEditorExtensionContent';
import { useBotEditorPanels } from './useBotEditorPanels';
import { CapabilityPanel } from '@/components/BotWorkshop/Editor/CapabilityPanel';
import { ChannelConfigPanel } from '@/components/BotWorkshop/Editor/ChannelConfigPanel';
import { DetailDebugChat } from './DetailDebugChat';
import { DwsHostingPanel } from '@/components/BotWorkshop/Editor/DwsHostingPanel';
import { IdentityConfigPanel } from '@/components/BotWorkshop/Editor/IdentityConfigPanel';
import { LinkPanel } from '@/components/BotWorkshop/Editor/LinkPanel';
import { MoreConfigPanel } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import { RenderScreenPanel } from '@/components/BotWorkshop/Editor/RenderScreenPanel';
import { ResourcePanel } from '@/components/BotWorkshop/Editor/ResourcePanel';
import { RoutinePanel } from '@/components/BotWorkshop/Editor/RoutinePanel';
import TaskEscort from '@/components/BotWorkshop/TaskEscort';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import { isSameHumanIdentity } from '@/domain/userIdentity';
import { useBotAdvancedConfig } from '@/hooks/useBotAdvancedConfig';
import { useBotEditor } from '@/hooks/useBotEditor';
import { useBotWorkshopDetail } from '@/hooks/useBotWorkshopDetail';
import { useBotWorkshopEditorIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useDesktopFolder } from '@/hooks/useDesktopFolder';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { isServiceVerification } from '@/services/botWorkshop/botPolicy';
import { desktopCapabilityPolicy } from '@/services/botWorkshop/desktopCapabilityPolicy';
import { cn } from '@/utils/cn';
import React, { useEffect, useState } from 'react';
import { DetailHeader } from './DetailHeader';
import { DetailNavigation } from './DetailNavigation';
import { moreTabs } from './detailTabs';
import { useHostingTabs } from './useHostingTabs';
import { Points } from '@/tracking/points';import { Tracert } from '@/utils/tracert';
const BotWorkshopDetailPage: React.FC = () => {
  const { id, ownerId, requestedEditable, runtimeStage } = useBotEditorAddress();
  const openDesktopFolder = useDesktopFolder(id);
  const currentSpaceId = useSpaceContext((state) => state.currentSpaceId);
  const requestIdentity = useBotWorkshopEditorIdentity();
  const detail = useBotWorkshopDetail(id, requestedEditable, requestIdentity.ready, ownerId, requestIdentity.userId);
  const verifying =
    !!detail.bot &&
    detail.bot.serviceMode === 'service' &&
    (runtimeStage === 'verify' || isServiceVerification(detail.bot));
  const editable = requestedEditable && !verifying;
  const addressedOwnerId = ownerId ?? detail.bot?.ownerId;
  const isOwner = isSameHumanIdentity(addressedOwnerId, requestIdentity.userId);
  const editor = useBotEditor(
    id,
    detail.bot?.serviceMode === 'service',
    currentSpaceId === undefined ? undefined : String(currentSpaceId),
    requestIdentity.ready && detail.bot?.id === id,
    addressedOwnerId,
    isOwner,
    detail.bot?.deployment,
    detail.bot?.runtime.engine,
  );
  const desktopPolicy = desktopCapabilityPolicy(detail.bot?.deployment ?? '', detail.bot?.runtime.engine ?? '');
  const advanced = useBotAdvancedConfig(
    id,
    requestIdentity.ready && !!detail.bot,
    ownerId,
    desktopPolicy.markdown,
    desktopPolicy.channels,
  );
  const { panels, additionalTabs } = useBotEditorPanels();
  const { tab, setTab, more, setMore, hostingAllowed, visibleMainTabs } = useHostingTabs(
    id,
    requestIdentity.userId,
    detail.bot,
    isOwner,
    desktopPolicy.routines,
    additionalTabs,
    JSON.stringify([addressedOwnerId, runtimeStage, currentSpaceId]),
  );
  const ExtensionPanel = panels.find((panel) => `extension:${panel.id}` === tab)?.Panel;
  const [debugOpen, setDebugOpen] = useState(true); const [debugFullscreen, setDebugFullscreen] = useState(false);
  useEffect(() => {
    setDebugOpen(true);
    setDebugFullscreen(false);
  }, [id]);
  if (!id)
    return (
      <Empty
        title="缺少 Bot 标识"
        description="请从 Bot 管理重新进入。"
        action={<Button onClick={detail.back}>返回 Bot 管理</Button>}
      />
    );
  if (requestIdentity.loading) return <Spin tip="正在获取当前用户身份…" />;
  if (requestIdentity.error)
    return (
      <Empty
        title="无法加载用户身份"
        description={requestIdentity.error}
        action={<Button onClick={detail.back}>返回 Bot 管理</Button>}
      />
    );
  if (detail.loading) return <Spin tip="加载 Bot 配置…" />;
  if (detail.error || !detail.bot)
    return (
      <Empty
        title="无法查看 Bot"
        description={detail.error ?? 'Bot 不存在或无权访问'}
        action={<Button onClick={detail.back}>返回 Bot 管理</Button>}
      />
    );
  const bot = detail.bot;
  const visibleMoreTabs = moreTabs.filter(
    (item) =>
      ({
        engine: desktopPolicy.engineConfig && isOwner,
        md: desktopPolicy.markdown,
        node: desktopPolicy.nodes,
        channel: desktopPolicy.channels,
        screen: desktopPolicy.screens,
      }[item.key]),
  );
  return (
    <main className="bot-editor-shell flex h-full min-h-0 flex-col bg-background">
      <DetailHeader
        bot={bot}
        editable={editable}
        isOwner={isOwner}
        debugOpen={debugOpen}
        saving={detail.saving}
        onBack={detail.back}
        onOpenDebug={() => setDebugOpen(true)}
        onSaveBasic={async (input) => {
          Tracert.click(Points.botWorkshop.saveConfig, { bot_id: id }); await detail.save(input);
        }}
        onReload={() => void detail.retry()}
      />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <DetailNavigation
          hidden={debugFullscreen}
          mainTabs={visibleMainTabs}
          moreTabs={visibleMoreTabs}
          activeTab={tab}
          activeMore={more}
          onTabChange={(nextTab) => {
            setTab(nextTab);
            setMore(undefined);
          }}
          onMoreChange={(nextMore) => {
            setMore(nextMore);
            if (nextMore === 'engine') void editor.loadEngineConfig();
          }}
        />
        <section
          className={cn(
            'app-scrollbar min-w-0 flex-1 overflow-y-auto bg-background lg:max-w-none',
            debugOpen && !debugFullscreen && 'border-r border-border lg:w-[56%] lg:flex-none',
            debugFullscreen && 'hidden',
          )}
        >
          {verifying ? (
            <p className="border-b border-border bg-muted px-6 py-3 text-sm text-muted-foreground">
              预发验证中，配置已锁定；请在右侧对话区验证 Bot。
            </p>
          ) : null}
          <fieldset
            disabled={verifying}
            className={cn('min-w-0 border-0 p-0', verifying && 'pointer-events-none opacity-50')}
          >
            {editor.loading ? (
              <Spin tip="加载编辑配置…" />
            ) : more === 'md' ? (
              <IdentityConfigPanel
                files={advanced.files}
                editable={editable}
                getFile={advanced.getFile}
                onSave={advanced.saveFile}
              />
            ) : more === 'channel' ? (
              <ChannelConfigPanel
                channels={advanced.channels}
                editable={editable}
                serviceBot={bot.serviceMode === 'service'}
                onCreate={advanced.createChannel}
                onUpdate={advanced.updateChannel}
                onToggle={advanced.toggleChannel}
                onDelete={advanced.deleteChannel}
              />
            ) : more === 'screen' ? (
              <RenderScreenPanel
                screens={editor.screens}
                editable={editable}
                onSave={editor.saveScreen}
                onDelete={editor.deleteScreen}
              />
            ) : more === 'engine' && editor.engineConfigLoading ? (
              <Spin tip="正在加载引擎配置…" />
            ) : more ? (
              <MoreConfigPanel
                tab={more}
                config={editor.engineConfig}
                editable={editable}
                engineStatus={editor.engineStatus}
                restoringDefaults={editor.restoringDefaultEngineConfig}
                onConfigChange={editor.setEngineConfig}
                onSave={editor.saveEngineConfig}
                onRestoreDefaults={editor.restoreDefaultEngineConfig}
              />
            ) : ExtensionPanel ? (
              <BotEditorExtensionContent Panel={ExtensionPanel} botId={bot.id} ownerId={addressedOwnerId}
                spaceId={currentSpaceId === undefined ? undefined : String(currentSpaceId)}
                serviceMode={bot.serviceMode} runtimeStage={runtimeStage} editable={editable} />
            ) : tab === 'hosting' && hostingAllowed ? (
              <DwsHostingPanel key={`${bot.id}:${bot.ownerId}`} botId={bot.id} />
            ) : tab === 'capability' ? (
              <CapabilityPanel
                compact={debugOpen && !debugFullscreen}
                desktop={bot.deployment === 'local'}
                onLocalToggle={editor.toggleSkill}
                onLocalDelete={editor.deleteSkill}
                onLocalUpload={editor.uploadSkill}
                botId={bot.id}
                ownerId={bot.ownerId}
                skillSets={editor.skillSets}
                mySkills={editor.skills.filter((skill) => skill.source === 'local')}
                availableMcps={editor.availableMcps}
                marketSkills={editor.marketSkills}
                skillCenterSkills={editor.skillCenterSkills}
                workshopSkills={editor.workshopSkills}
                editable={editable}
                onCreate={editor.createSkillSet}
                onDelete={editor.deleteSkillSet}
                onActive={editor.setSkillSetActive}
                pendingSkillSetToggle={editor.pendingSkillSetToggle}
                onSkill={editor.setSkillSetSkill}
                onSkillCenterReferences={editor.addSkillCenterReferences}
                onUploadSkillFolder={editor.uploadSkillFolder}
                onLoadCandidates={editor.loadCapabilityCandidates}
                candidatesLoading={editor.candidatesLoading}
                onMcp={editor.setSkillSetMcp}
                mcpCallTypes={editor.mcpCallTypes}
                callerContextEditable={editable && editor.callerContextEditable}
                mcpIdentityDisabledReason={
                  bot.serviceMode === 'service' ? undefined : '个人 Bot 固定使用 Owner 模式，不支持切换 MCP 访问方式'
                }
                updatingCallType={editor.updatingCallType}
                onMcpCallType={editor.updateMcpCallType}
              />
            ) : tab === 'resource' ? (
              <>
                <ResourcePanel
                  desktop={bot.deployment === 'local'}
                  onOpenFolder={openDesktopFolder}
                  resources={editor.resources}
                  editable={editable && desktopPolicy.resourceWritable}
                  onCreateDirectory={editor.createDirectory}
                  onDelete={editor.deleteResource}
                  onUpload={editor.uploadResource}
                  onPreview={editor.previewResource}
                  onCopyPath={editor.copyResourcePath}
                  onDownload={editor.downloadResource}
                  onLoadDirectory={editor.loadResourceDirectory}
                  loadingPaths={editor.resourceLoadingPaths}
                />
                {bot.deployment === 'local' ? <LinkPanel botId={bot.id} editable={editable && isOwner} /> : null}
              </>
            ) : tab === 'routine' ? (
              <RoutinePanel
                routines={editor.routines}
                editable={editable}
                onLoadModels={editor.loadRoutineModels}
                onSave={editor.saveRoutine}
                onToggle={editor.toggleRoutine}
                onDelete={editor.deleteRoutine}
              />
            ) : (
              <TaskEscort bot={bot} />
            )}
          </fieldset>
        </section>
        {debugOpen ? (
          <DetailDebugChat
            authenticatedUserId={requestIdentity.ready ? requestIdentity.userId ?? null : null}
            key={`${bot.id}:${verifying ? 'verify' : runtimeStage ?? bot.lifecycle}`}
            bot={bot}
            runtimeStage={verifying ? 'verify' : runtimeStage}
            ownerId={addressedOwnerId}
            fullscreen={debugFullscreen}
            onClose={() => {
              setDebugOpen(false);
              setDebugFullscreen(false);
            }}
            onToggleFullscreen={() => setDebugFullscreen((current) => !current)}
          />
        ) : null}
      </div>
    </main>
  );
};
export default BotWorkshopDetailPage;
