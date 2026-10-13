import { CapabilityPanel } from '@/components/BotWorkshop/Editor/CapabilityPanel';
import { ChannelConfigPanel } from '@/components/BotWorkshop/Editor/ChannelConfigPanel';
import { DebugChatPanel } from '@/components/BotWorkshop/Editor/DebugChatPanel';
import { IdentityConfigPanel } from '@/components/BotWorkshop/Editor/IdentityConfigPanel';
import { LinkPanel } from '@/components/BotWorkshop/Editor/LinkPanel';
import { MoreConfigPanel, type MoreConfigTab } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import { RenderScreenPanel } from '@/components/BotWorkshop/Editor/RenderScreenPanel';
import { ResourcePanel } from '@/components/BotWorkshop/Editor/ResourcePanel';
import { RoutinePanel } from '@/components/BotWorkshop/Editor/RoutinePanel';
import TaskEscort from '@/components/BotWorkshop/TaskEscort';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { isSameHumanIdentity } from '@/domain/userIdentity';
import { useBotAdvancedConfig } from '@/hooks/useBotAdvancedConfig';
import { useBotEditor } from '@/hooks/useBotEditor';
import { useBotWorkshopDetail } from '@/hooks/useBotWorkshopDetail';
import { useBotWorkshopEditorIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useDesktopFolder } from '@/hooks/useDesktopFolder';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { desktopCapabilityPolicy } from '@/services/botWorkshop/desktopCapabilityPolicy';
import { cn } from '@/utils/cn';
import { useLocation } from '@umijs/max';
import React, { useEffect, useState } from 'react';
import { DetailHeader } from './DetailHeader';
import { mainTabs, moreTabs, type MainTab } from './detailTabs';

const BotWorkshopDetailPage: React.FC = () => {
  const params = new URLSearchParams(useLocation().search);
  const id = params.get('id');
  const openDesktopFolder = useDesktopFolder(id);
  const ownerId = params.get('owner_id')?.trim() || undefined;
  const editable = params.get('type') === 'edit';
  const runtimeStageParam = params.get('runtime_stage');
  const runtimeStage: BotRuntimeStage | undefined =
    runtimeStageParam === 'draft' || runtimeStageParam === 'verify' || runtimeStageParam === 'online'
      ? runtimeStageParam
      : undefined;
  const currentSpaceId = useSpaceContext((state) => state.currentSpaceId);
  const requestIdentity = useBotWorkshopEditorIdentity();
  const detail = useBotWorkshopDetail(id, editable, requestIdentity.ready, ownerId);
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
  const [tab, setTab] = useState<MainTab>('capability');
  const [more, setMore] = useState<MoreConfigTab>();
  const [debugOpen, setDebugOpen] = useState(true);
  useEffect(() => {
    if (!isOwner && more === 'engine') setMore(undefined);
  }, [isOwner, more]);
  useEffect(() => {
    setTab('capability');
    setMore(undefined);
    setDebugOpen(true);
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
        onSaveBasic={detail.save}
        onReload={() => void detail.retry()}
      />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <nav
          aria-label="Bot 编辑模块"
          className="bot-editor-nav app-scrollbar flex w-20 shrink-0 flex-col items-center gap-2 overflow-y-auto border-r border-border bg-card py-3"
        >
          {mainTabs
            .filter((item) => item.key !== 'routine' || desktopPolicy.routines)
            .map((item) => (
              <Button
                key={item.key}
                variant="ghost"
                size="icon"
                className={cn(
                  'h-16 w-[60px] flex-col gap-1 rounded-lg text-xs font-normal text-muted-foreground',
                  tab === item.key && !more && 'bg-muted text-foreground hover:bg-muted hover:text-foreground',
                )}
                aria-label={item.label}
                onClick={() => {
                  setTab(item.key);
                  setMore(undefined);
                }}
              >
                {item.icon}
                <span>{item.label}</span>
              </Button>
            ))}
          {moreTabs
            .filter(
              (item) =>
                ({
                  engine: desktopPolicy.engineConfig && isOwner,
                  md: desktopPolicy.markdown,
                  node: desktopPolicy.nodes,
                  channel: desktopPolicy.channels,
                  screen: desktopPolicy.screens,
                }[item.key]),
            )
            .map((item) => (
              <Button
                key={item.key}
                variant="ghost"
                size="icon"
                className={cn(
                  'h-16 w-[60px] flex-col gap-1 rounded-lg text-xs font-normal text-muted-foreground',
                  more === item.key && 'bg-muted text-foreground hover:bg-muted hover:text-foreground',
                )}
                aria-label={item.label}
                onClick={() => {
                  setMore(item.key);
                  if (item.key === 'engine') void editor.loadEngineConfig();
                }}
              >
                {item.icon}
                <span>{item.label}</span>
              </Button>
            ))}
        </nav>
        <section
          className={cn(
            'app-scrollbar min-w-0 flex-1 bg-background overflow-y-auto lg:max-w-none',
            debugOpen && 'border-r border-border lg:w-[56%] lg:flex-none',
          )}
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
          ) : tab === 'capability' ? (
            <CapabilityPanel
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
              callerContextEditable={editor.callerContextEditable}
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
        </section>
        {debugOpen ? (
          <div className="hidden min-w-0 flex-1 lg:flex">
            <DebugChatPanel
              bot={bot}
              runtimeStage={runtimeStage}
              ownerId={addressedOwnerId}
              onClose={() => setDebugOpen(false)}
            />
          </div>
        ) : null}
      </div>
    </main>
  );
};
export default BotWorkshopDetailPage;
