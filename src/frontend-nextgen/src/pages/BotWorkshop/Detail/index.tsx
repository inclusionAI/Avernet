import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { CapabilityPanel } from '@/components/BotWorkshop/Editor/CapabilityPanel';
import { ChannelConfigPanel } from '@/components/BotWorkshop/Editor/ChannelConfigPanel';
import { DebugChatPanel } from '@/components/BotWorkshop/Editor/DebugChatPanel';
import { IdentityConfigPanel } from '@/components/BotWorkshop/Editor/IdentityConfigPanel';
import { MoreConfigPanel, type MoreConfigTab } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import { RenderScreenPanel } from '@/components/BotWorkshop/Editor/RenderScreenPanel';
import { ResourcePanel } from '@/components/BotWorkshop/Editor/ResourcePanel';
import { RoutinePanel } from '@/components/BotWorkshop/Editor/RoutinePanel';
import TaskEscort from '@/components/BotWorkshop/TaskEscort';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Spin } from '@/components/ui/Spin';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { useBotAdvancedConfig } from '@/hooks/useBotAdvancedConfig';
import { useBotEditor } from '@/hooks/useBotEditor';
import { useBotWorkshopDetail } from '@/hooks/useBotWorkshopDetail';
import { useBotWorkshopEditorIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { useLocation } from '@umijs/max';
import {
  ArrowLeft,
  Clock,
  Database,
  FileText,
  LayoutGrid,
  Network,
  Save,
  Settings,
  ShieldCheck,
  Smartphone,
} from 'lucide-react';
import React, { useState } from 'react';

type MainTab = 'capability' | 'resource' | 'routine' | 'escort';
const mainTabs: Array<{ key: MainTab; label: string; icon: React.ReactNode }> = [
  { key: 'capability', label: '能力集', icon: <LayoutGrid className="size-5" /> },
  { key: 'resource', label: '资源', icon: <Database className="size-5" /> },
  { key: 'routine', label: '定时任务', icon: <Clock className="size-5" /> },
  { key: 'escort', label: '任务护航', icon: <ShieldCheck className="size-5" /> },
];
const moreTabs: Array<{ key: MoreConfigTab; label: string; icon: React.ReactNode }> = [
  { key: 'engine', label: '引擎配置', icon: <Settings className="size-4" /> },
  { key: 'md', label: 'MD 文档', icon: <FileText className="size-4" /> },
  { key: 'node', label: '节点', icon: <Database className="size-4" /> },
  { key: 'channel', label: '渠道', icon: <Network className="size-4" /> },
  { key: 'approval', label: '发布审批', icon: <ShieldCheck className="size-4" /> },
  { key: 'screen', label: '副屏', icon: <Smartphone className="size-4" /> },
];

const BotWorkshopDetailPage: React.FC = () => {
  const params = new URLSearchParams(useLocation().search);
  const id = params.get('id');
  const editable = params.get('type') === 'edit';
  const runtimeStageParam = params.get('runtime_stage');
  const runtimeStage: BotRuntimeStage | undefined =
    runtimeStageParam === 'draft' || runtimeStageParam === 'verify' || runtimeStageParam === 'online'
      ? runtimeStageParam
      : undefined;
  const currentSpaceId = useSpaceContext((state) => state.currentSpaceId);
  const requestIdentity = useBotWorkshopEditorIdentity();
  const detail = useBotWorkshopDetail(id, editable, requestIdentity.ready);
  const editor = useBotEditor(
    id,
    detail.bot?.serviceMode === 'service',
    currentSpaceId === undefined ? undefined : String(currentSpaceId),
    requestIdentity.ready,
  );
  const advanced = useBotAdvancedConfig(id, requestIdentity.ready);
  const [tab, setTab] = useState<MainTab>('capability');
  const [more, setMore] = useState<MoreConfigTab>();
  const [moreOpen, setMoreOpen] = useState(false);
  if (!id)
    return (
      <Empty
        title="缺少 Bot 标识"
        description="请从 Bot 工坊重新进入。"
        action={<Button onClick={detail.back}>返回 Bot 工坊</Button>}
      />
    );
  if (requestIdentity.loading) return <Spin tip="正在获取当前用户身份…" />;
  if (requestIdentity.error)
    return (
      <Empty
        title="无法加载用户身份"
        description={requestIdentity.error}
        action={<Button onClick={detail.back}>返回 Bot 工坊</Button>}
      />
    );
  if (detail.loading) return <Spin tip="加载 Bot 配置…" />;
  if (detail.error || !detail.bot)
    return (
      <Empty
        title="无法查看 Bot"
        description={detail.error ?? 'Bot 不存在或无权访问'}
        action={<Button onClick={detail.back}>返回 Bot 工坊</Button>}
      />
    );
  const bot = detail.bot;
  return (
    <main className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-4 sm:px-6">
        <Button
          variant="ghost"
          size="icon"
          aria-label="返回 Bot 工坊"
          onClick={detail.back}
          leftIcon={<ArrowLeft className="size-4" />}
        />
        <BotAvatar name={bot.name} />
        <div className="min-w-0 flex-1">
          <h1 className="m-0 truncate text-base font-semibold">{bot.name}</h1>
          <p className="m-0 mt-0.5 text-xs text-muted-foreground">
            {bot.runtime.engine} · {bot.deployment === 'local' ? '本地' : '云端'}
          </p>
        </div>
        <Badge tone={editable ? 'primary' : 'neutral'}>{editable ? '编辑模式' : '只读模式'}</Badge>
        <Button leftIcon={<Save className="size-4" />} onClick={detail.back}>
          保存并退出
        </Button>
      </header>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <TooltipProvider delayDuration={300}>
          <nav
            aria-label="Bot 编辑模块"
            className="flex w-16 shrink-0 flex-col items-center gap-3 border-r border-border bg-muted/30 py-4"
          >
            {mainTabs.map((item) => (
              <Tooltip key={item.key}>
                <TooltipTrigger asChild>
                  <Button
                    variant={tab === item.key && !more ? 'primary' : 'ghost'}
                    size="icon"
                    aria-label={item.label}
                    leftIcon={item.icon}
                    onClick={() => {
                      setTab(item.key);
                      setMore(undefined);
                    }}
                  />
                </TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            ))}
            <Popover open={moreOpen} onOpenChange={setMoreOpen}>
              <PopoverTrigger asChild>
                <Button
                  variant={more ? 'secondary' : 'ghost'}
                  size="icon"
                  aria-label="更多配置"
                  leftIcon={<Settings className="size-5" />}
                />
              </PopoverTrigger>
              <PopoverContent side="right" align="end" className="w-48 space-y-1 p-2">
                {moreTabs.map((item) => (
                  <Button
                    key={item.key}
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start"
                    leftIcon={item.icon}
                    onClick={() => {
                      setMore(item.key);
                      setMoreOpen(false);
                    }}
                  >
                    {item.label}
                  </Button>
                ))}
              </PopoverContent>
            </Popover>
          </nav>
        </TooltipProvider>
        <section className="app-scrollbar min-w-0 flex-1 border-r border-border bg-background overflow-y-auto lg:w-[42%] lg:max-w-[720px] lg:flex-none">
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
          ) : more ? (
            <MoreConfigPanel
              tab={more}
              config={editor.engineConfig}
              editable={editable}
              engineStatus={editor.engineStatus}
              approvalRequired={editor.approvalRequired}
              serviceBot={bot.serviceMode === 'service'}
              onConfigChange={editor.setEngineConfig}
              onSave={editor.saveEngineConfig}
              onApprovalChange={editor.saveApproval}
            />
          ) : tab === 'capability' ? (
            <CapabilityPanel
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
              onSkill={editor.setSkillSetSkill}
              onSkillCenterReferences={editor.addSkillCenterReferences}
              onUploadSkillFolder={editor.uploadSkillFolder}
              onLoadCandidates={editor.loadCapabilityCandidates}
              onMcp={editor.setSkillSetMcp}
              mcpCallTypes={editor.mcpCallTypes}
              callerContextEditable={editor.callerContextEditable}
              updatingCallType={editor.updatingCallType}
              onMcpCallType={editor.updateMcpCallType}
            />
          ) : tab === 'resource' ? (
            <ResourcePanel
              resources={editor.resources}
              editable={editable}
              onCreateDirectory={editor.createDirectory}
              onDelete={editor.deleteResource}
              onUpload={editor.uploadResource}
              onPreview={editor.previewResource}
              onDownload={editor.downloadResource}
              onLoadDirectory={editor.loadResourceDirectory}
              loadingPaths={editor.resourceLoadingPaths}
            />
          ) : tab === 'routine' ? (
            <RoutinePanel
              routines={editor.routines}
              editable={editable}
              onSave={editor.saveRoutine}
              onToggle={editor.toggleRoutine}
              onDelete={editor.deleteRoutine}
              onRun={editor.runRoutine}
              runs={editor.routineRuns}
              onLoadRuns={editor.loadRoutineRuns}
            />
          ) : (
            <div className="p-5 sm:p-6">
              <TaskEscort bot={bot} />
            </div>
          )}
        </section>
        <div className="hidden min-w-0 flex-1 lg:flex">
          <DebugChatPanel bot={bot} runtimeStage={runtimeStage} />
        </div>
      </div>
    </main>
  );
};
export default BotWorkshopDetailPage;
