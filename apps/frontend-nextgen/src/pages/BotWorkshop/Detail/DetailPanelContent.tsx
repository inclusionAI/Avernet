// Bot 详情主面板区:从 index.tsx 拆出的「当前子菜单 → 面板」分发视图。
// 只做展示与分发,数据钩子仍留在页面(index.tsx)持有,避免两级重复拉取。
import type { BotEditorPanelContext } from '@/capabilities/types';
import type { ComponentType } from 'react';
import { CapabilityPanel } from '@/components/BotWorkshop/Editor/CapabilityPanel';
import { ChannelConfigPanel } from '@/components/BotWorkshop/Editor/ChannelConfigPanel';
import { DwsHostingPanel } from '@/components/BotWorkshop/Editor/DwsHostingPanel';
import { IdentityConfigPanel } from '@/components/BotWorkshop/Editor/IdentityConfigPanel';
import { LinkPanel } from '@/components/BotWorkshop/Editor/LinkPanel';
import { MoreConfigPanel } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import { RenderScreenPanel } from '@/components/BotWorkshop/Editor/RenderScreenPanel';
import { ResourcePanel } from '@/components/BotWorkshop/Editor/ResourcePanel';
import { RoutinePanel } from '@/components/BotWorkshop/Editor/RoutinePanel';
import TaskEscort from '@/components/BotWorkshop/TaskEscort';
import { Spin } from '@/components/ui/Spin';
import type { BotDomain, BotRuntimeStage } from '@/domain/botWorkshop';
import { useBotAdvancedConfig } from '@/hooks/useBotAdvancedConfig';
import { useBotEditor } from '@/hooks/useBotEditor';
import { useHostingTabs } from './useHostingTabs';
import { desktopCapabilityPolicy } from '@/services/botWorkshop/desktopCapabilityPolicy';
import { useDesktopFolder } from '@/hooks/useDesktopFolder';
import { BotEditorExtensionContent } from './BotEditorExtensionContent';
import { MainTab } from './detailTabs';

export function DetailPanelContent({
  bot,
  editor,
  advanced,
  desktopPolicy,
  more,
  tab,
  hostingAllowed,
  editable,
  isOwner,
  compact,
  ExtensionPanel,
  currentSpaceId,
  runtimeStage,
  ownerId,
  onOpenFolder,
}: {
  bot: BotDomain;
  editor: ReturnType<typeof useBotEditor>;
  advanced: ReturnType<typeof useBotAdvancedConfig>;
  desktopPolicy: ReturnType<typeof desktopCapabilityPolicy>;
  more: ReturnType<typeof useHostingTabs>['more'];
  tab: MainTab;
  hostingAllowed: ReturnType<typeof useHostingTabs>['hostingAllowed'];
  editable: boolean;
  isOwner: boolean;
  /** 预发验证/全屏等锁定形态下Capability/Routine 使用紧凑布局 */
  compact: boolean;
  /** 能力位注入的扩展面板构造器；无扩展时缺省 */
  ExtensionPanel?: ComponentType<BotEditorPanelContext>;
  currentSpaceId: number | undefined;
  runtimeStage: BotRuntimeStage | undefined;
  /** 目录地址参数(或 Bot Owner)对应的归属人 */
  ownerId?: string;
  onOpenFolder: ReturnType<typeof useDesktopFolder>;
}) {
  return editor.loading ? (
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
    <BotEditorExtensionContent
      Panel={ExtensionPanel}
      botId={bot.id}
      ownerId={ownerId}
      spaceId={currentSpaceId === undefined ? undefined : String(currentSpaceId)}
      serviceMode={bot.serviceMode}
      runtimeStage={runtimeStage}
      editable={editable}
    />
  ) : tab === 'hosting' && hostingAllowed ? (
    <DwsHostingPanel key={`${bot.id}:${bot.ownerId}`} botId={bot.id} />
  ) : tab === 'capability' ? (
    <CapabilityPanel
      compact={compact}
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
        onOpenFolder={onOpenFolder}
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
      compact={compact}
      pendingRoutineToggle={editor.pendingRoutineToggle}
      onLoadModels={editor.loadRoutineModels}
      onSave={editor.saveRoutine}
      onToggle={editor.toggleRoutine}
      onDelete={editor.deleteRoutine}
    />
  ) : (
    <TaskEscort bot={bot} />
  );
}
