/** useBotSessionFilesFeature — 装配单聊会话文件的上传/管理/引用 + /clear /skill 命令,
 *  返回 ChatPanel 所需的 fileChip / command / fileToolbar / senderRef / 副屏面板节点。 */
import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import { BotSessionFileOverlays } from '@/pages/Workspace/components/BotSessionFiles/BotSessionFileOverlays';
import { buildBotSessionCommandItems } from '@/pages/Workspace/hooks/botSessionCommandItems';
import { useBotCapabilitySets } from '@/pages/Workspace/hooks/useBotCapabilitySets';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { resolveChatRuntimeStage, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
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
  /** 「通用协作能力」能力集只读查看装配（我的/团队 Bot 非空；好友 Bot 为 null，
   *  「通用协作能力」文案区随之不渲染，规格见 add-composer-capability-set-viewer）。 */
  capabilitySets: ComposerCapabilitySetsState | null;
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
  // Only shared service conversations opt into online; my Bots retain the existing file request shape.
  const stage =
    bot?.botType === 'service' && (bot.isTeamBot || bot.isFriendBot) ? resolveChatRuntimeStage(bot) : undefined;

  const files = useBotSessionFiles(
    botId,
    sessionId,
    userId,
    ownerId,
    bot?.isFriendBot ? userId ?? undefined : undefined,
    ...(stage ? ([stage] as const) : []),
  );
  const upload = useBotSessionFileUpload(botId, sessionId, userId, ownerId, ...(stage ? ([stage] as const) : []));
  const skillsState = useBotSkills(bot, userId);
  // 「通用协作能力」能力集装配：好友 Bot / 不可会话 Bot 为 null（文案区随之隐藏）。
  const capabilitySets = useBotCapabilitySets(bot, userId);
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

  // /file 子面板动态拉取(2026-10-21 懒加载):每次唤起都刷新最新清单(数据最新鲜);
  // query 为 /file 后的过滤词(空串=刚唤起)。refresh 后从 store 读最新列表(闭包快照滞后)。
  const onSearchFiles = useCallback(
    async (query: string): Promise<CommandItem[]> => {
      await files.refresh();
      const { readyFiles } = useBotSessionFileStore.getState();
      const keyword = query.trim().toLowerCase();
      const matched = keyword
        ? readyFiles.filter((f) => f.displayName.toLowerCase().includes(keyword))
        : readyFiles;
      return matched.map((f) => ({
        id: `__file__${f.resourceId}`,
        name: f.displayName,
        description: '引用到输入框',
        icon: <FileIcon className="h-3.5 w-3.5 text-muted-foreground" />,
        data: { resource_id: f.resourceId, display_name: f.displayName },
        preventInsert: true,
        onSelect: () => handleReference(f),
      }));
    },
    [files, handleReference],
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
        onSearchFiles,
        skillsLoading: skillsState.isLoading,
        filesLoading: files.isLoadingList,
        onClear,
        onSelectSkill: handleSelectSkill,
      }),
    [canUseSkills, skillItems, onSearchFiles, skillsState.isLoading, files.isLoadingList, onClear, handleSelectSkill],
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
    <BotSessionFileOverlays
      botId={botId}
      sessionId={sessionId}
      userId={userId}
      ownerId={ownerId}
      stage={stage}
      files={files}
      upload={upload}
      drawerOpen={drawerOpen}
      uploadOpen={uploadOpen}
      onCloseDrawer={closeFileDrawer}
      onCloseUpload={() => setUploadOpen(false)}
      onOpenUpload={openUpload}
      onAddToSession={addReadyUploadsToSession}
      onReference={handleReference}
    />
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
    capabilitySets,
  };
}
