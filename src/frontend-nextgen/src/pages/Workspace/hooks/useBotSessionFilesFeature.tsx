/** useBotSessionFilesFeature — 装配单聊会话文件的上传/管理/引用 + /clear /skill 命令,
 *  返回 ChatPanel 所需的 fileChip / command / fileToolbar / senderRef / 副屏面板节点。 */
import { BotSessionFilesPanel } from '@/pages/Workspace/components/BotSessionFiles/BotSessionFilesPanel';
import { BotUploadFilesModal } from '@/pages/Workspace/components/BotSessionFiles/UploadFilesModal';
import { ResizableWorkspaceSidebar } from '@/pages/Workspace/components/ResizableWorkspaceSidebar';
import { buildBotSessionCommandItems } from '@/pages/Workspace/hooks/botSessionCommandItems';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { CommandConfig, CommandItem, FileChipConfig, PendingFileChip, SenderRef } from '@tc-chat/ui/es/Sender';
import { FileIcon, Zap } from 'lucide-react';
import { useCallback, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface UseBotSessionFilesFeatureResult {
  senderRef: React.RefObject<SenderRef | null>;
  fileChip: FileChipConfig;
  command: CommandConfig;
  fileToolbar: React.ReactNode;
  featureNode: React.ReactNode;
  /** 打开文件管理副屏（验收微调：入口迁至顶栏，供 ChatPanel onManageFiles 调用）。 */
  openFileDrawer: () => void;
  /** 关闭文件管理副屏（供 ChatPanel 顶栏入口的选中态切换）。 */
  closeFileDrawer: () => void;
  /** 文件管理副屏当前是否打开（供顶栏入口选中态反馈）。 */
  fileDrawerOpen: boolean;
  /** 打开文件上传 Modal（供 ComposerCapabilitiesMenu onUpload 调用）。 */
  openUpload: () => void;
}

export function useBotSessionFilesFeature(
  bot: ChatBotView | null,
  session: BotChatSessionView | null,
  userId: string | null,
  onClear: () => Promise<void>,
): UseBotSessionFilesFeatureResult {
  const botId = bot?.botType === 'desktop' ? null : bot?.realBotId ?? null;
  const sessionId = session?.sessionId ?? null;
  const ownerId = bot?.ownerId;

  const files = useBotSessionFiles(botId, sessionId, userId, ownerId);
  const upload = useBotSessionFileUpload(botId, sessionId, userId, ownerId);
  const skillsState = useBotSkills(bot, userId);
  const senderRef = useRef<SenderRef | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  const insertChips = useCallback((file: BotSessionFileView) => {
    const chips: PendingFileChip[] = [{ resource_id: file.resourceId, display_name: file.displayName }];
    senderRef.current?.insertFileChips?.(chips);
    senderRef.current?.focus?.();
    setDrawerOpen(false);
    toast.success(`已引用「${file.displayName}」`);
  }, []);

  const handleReference = useCallback(
    (file: BotSessionFileView) => {
      if (file.status !== 'ready') {
        toast.warning('文件未就绪,暂不可引用');
        return;
      }
      insertChips(file);
    },
    [insertChips],
  );

  const addReadyUploadsToSession = useCallback(() => {
    const readyTasks = upload.tasks.filter((task) => task.phase === 'ready' && task.resourceId);
    if (readyTasks.length === 0) {
      toast.warning('暂无已完成上传的文件可添加');
      return;
    }
    senderRef.current?.insertFileChips?.(
      readyTasks.map((task) => ({ resource_id: task.resourceId as string, display_name: task.name })),
    );
    senderRef.current?.focus?.();
    readyTasks.forEach((task) => upload.removeTask(task.localId));
    setUploadOpen(false);
    toast.success(`已添加 ${readyTasks.length} 个文件至会话`);
  }, [upload.removeTask, upload.tasks]);

  const handleSelectSkill = useCallback((skillName: string) => {
    senderRef.current?.insert(`/${skillName} `, 'end');
    senderRef.current?.focus?.();
  }, []);

  const enabled = !!botId && !!sessionId && !!userId;
  const canUseSkills = !bot?.isFriendBot;

  const fileChip = useMemo<FileChipConfig>(
    () => ({
      label: '文件',
      icon: <FileIcon className="h-4 w-4" />,
      onPick: () => {
        if (!enabled) return;
        setDrawerOpen(true);
      },
      onRemove: () => {},
    }),
    [enabled],
  );

  const fileSubItems = useMemo<CommandItem[]>(
    () =>
      files.readyFiles.map((f) => ({
        id: `__file__${f.resourceId}`,
        name: f.displayName,
        description: '引用到输入框',
        icon: <FileIcon className="h-3.5 w-3.5 text-muted-foreground" />,
        data: { resource_id: f.resourceId, display_name: f.displayName },
        preventInsert: true,
        onSelect: () => handleReference(f),
      })),
    [files.readyFiles, handleReference],
  );

  const skillItems = useMemo<CommandItem[]>(
    () =>
      skillsState.skills.map((s) => ({
        id: `__skill__${s.skillId}`,
        name: s.name,
        description: s.description || (s.active ? '已激活' : '未激活'),
        icon: <Zap className="h-3.5 w-3.5 text-muted-foreground" />,
        data: { skill_id: s.skillId },
      })),
    [skillsState.skills],
  );

  // /skill /file /clear 命令项装配抽取到 botSessionCommandItems.tsx（纯配置，行为不变）。
  const commandItems = useMemo<CommandItem[]>(
    () =>
      buildBotSessionCommandItems({
        canUseSkills,
        skillItems,
        fileSubItems,
        skillsLoading: skillsState.isLoading,
        filesLoading: files.isLoadingList,
        onClear,
        onSelectSkill: handleSelectSkill,
      }),
    [canUseSkills, skillItems, fileSubItems, skillsState.isLoading, files.isLoadingList, onClear, handleSelectSkill],
  );

  const command = useMemo<CommandConfig>(
    () => ({
      categories: [
        {
          key: '__bot_commands__',
          label: '命令',
          items: commandItems,
        },
      ],
      onSelect: () => {},
      format: () => '',
    }),
    [commandItems],
  );

  // 上传文件仍在 ComposerCapabilitiesMenu（onUpload）；文件管理入口已迁至单聊顶栏
  // （ChatPanel onManageFiles → openFileDrawer），fileToolbar 不渲染独立按钮。
  const fileToolbar = useMemo(() => null, []);

  const openFileDrawer = useCallback(() => setDrawerOpen(true), []);
  const closeFileDrawer = useCallback(() => setDrawerOpen(false), []);
  const openUpload = useCallback(() => setUploadOpen(true), []);

  const featureNode = (
    <>
      <BotUploadFilesModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        queue={upload.tasks}
        isUploading={upload.isUploading}
        stageFiles={upload.stageFiles}
        submit={async () => {
          await upload.submit();
        }}
        onAddToSession={addReadyUploadsToSession}
        removeTask={upload.removeTask}
      />
      {drawerOpen && (
        // 验收微调：单聊文件管理由全屏 Modal 改为右侧副屏（与协作群 SessionFilesPanel 同构容器）。
        <ResizableWorkspaceSidebar
          ariaLabel="会话文件面板"
          side="right"
          minWidth={320}
          maxWidth={600}
          defaultWidth={380}
          storageKey="teamclaw:bot-files-panel-width"
          className="z-30 bg-background"
        >
          <BotSessionFilesPanel
            sessionName={session?.title || bot?.displayName || '当前会话'}
            readyFiles={files.readyFiles}
            isLoadingList={files.isLoadingList}
            botId={botId ?? undefined}
            sessionId={sessionId ?? undefined}
            userId={userId ?? undefined}
            ownerId={ownerId}
            onClose={closeFileDrawer}
            onUploadClick={() => setUploadOpen(true)}
            onOpen={() => void files.refresh()}
            onDelete={(f) => void files.deleteFile(f)}
            onDownload={(f) => void files.downloadFile(f)}
            onReference={handleReference}
          />
        </ResizableWorkspaceSidebar>
      )}
    </>
  );

  return {
    senderRef,
    fileChip,
    command,
    fileToolbar,
    featureNode,
    openFileDrawer,
    closeFileDrawer,
    fileDrawerOpen: drawerOpen,
    openUpload,
  };
}
