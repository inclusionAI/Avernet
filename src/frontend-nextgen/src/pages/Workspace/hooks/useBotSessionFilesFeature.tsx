/** useBotSessionFilesFeature — 装配单聊会话文件的上传/管理/引用 + /clear /skill 命令,
 *  返回 ChatPanel 所需的 fileChip / command / fileToolbar / senderRef / 弹窗节点。 */
import { BotSessionFilesModal } from '@/pages/Workspace/components/BotSessionFiles/BotSessionFilesModal';
import { BotUploadFilesModal } from '@/pages/Workspace/components/BotSessionFiles/UploadFilesModal';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { CommandConfig, CommandItem, FileChipConfig, PendingFileChip, SenderRef } from '@tc-chat/ui/es/Sender';
import { Eraser, FileIcon, Zap } from 'lucide-react';
import { useCallback, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface UseBotSessionFilesFeatureResult {
  senderRef: React.RefObject<SenderRef | null>;
  fileChip: FileChipConfig;
  command: CommandConfig;
  fileToolbar: React.ReactNode;
  featureNode: React.ReactNode;
  /** 打开文件管理 Modal（供 ComposerCapabilitiesMenu onManageFiles 调用）。 */
  openFileDrawer: () => void;
  /** 打开文件上传 Modal（供 ComposerCapabilitiesMenu onUpload 调用）。 */
  openUpload: () => void;
}

export function useBotSessionFilesFeature(
  bot: ChatBotView | null,
  session: BotChatSessionView | null,
  userId: string | null,
  onClear: () => Promise<void>,
): UseBotSessionFilesFeatureResult {
  const botId = bot?.realBotId ?? null;
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

  const handleSelectSkill = useCallback((skillName: string) => {
    senderRef.current?.insert(`/${skillName} `, 'end');
    senderRef.current?.focus?.();
  }, []);

  const enabled = !!botId && !!sessionId && !!userId;

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
        icon: <FileIcon className="h-3.5 w-3.5 text-[var(--color-muted)]" />,
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
        icon: <Zap className="h-3.5 w-3.5 text-[var(--color-muted)]" />,
        data: { skill_id: s.skillId },
      })),
    [skillsState.skills],
  );

  const command = useMemo<CommandConfig>(
    () => ({
      categories: [
        {
          key: '__bot_commands__',
          label: '命令',
          items: [
            {
              id: '__skill_entry__',
              name: 'skill',
              description: '唤起技能面板',
              icon: <Zap className="h-3.5 w-3.5 text-[var(--color-muted)]" />,
              preventInsert: true,
              subConfig: {
                label: '技能',
                categories: [
                  {
                    key: '__skill_list__',
                    label: '技能',
                    icon: <Zap className="h-3.5 w-3.5" />,
                    items: skillItems,
                    emptyText: skillsState.isLoading ? '加载中…' : '暂无可用技能',
                  },
                ],
                onSelect: (item) => {
                  if (item.name) handleSelectSkill(item.name);
                },
                format: (item) => `/${item.name}`,
              },
            },
            {
              id: '__file_entry__',
              name: 'file',
              description: '引用本会话已上传的文件',
              icon: <FileIcon className="h-3.5 w-3.5 text-[var(--color-muted)]" />,
              preventInsert: true,
              subConfig: {
                label: '文件',
                categories: [
                  {
                    key: '__session_file__',
                    label: '文件',
                    icon: <FileIcon className="h-3.5 w-3.5" />,
                    items: fileSubItems,
                    emptyText: files.isLoadingList ? '加载中…' : '暂无可引用文件,请先上传',
                  },
                ],
                onSelect: () => {},
                format: () => '',
              },
            },
            {
              id: '__clear__',
              name: 'clear',
              description: '清空当前会话上下文',
              icon: <Eraser className="h-3.5 w-3.5 text-[var(--color-muted)]" />,
              preventInsert: true,
              onSelect: () => void onClear(),
            },
          ],
        },
      ],
      onSelect: () => {},
      format: () => '',
    }),
    [skillItems, fileSubItems, skillsState.isLoading, files.isLoadingList, onClear, handleSelectSkill],
  );

  // 上传文件/文件管理已移入 ComposerCapabilitiesMenu（onUpload/onManageFiles），
  // fileToolbar 不再渲染独立按钮，避免与 + 号菜单重复。
  const fileToolbar = useMemo(() => null, []);

  const openFileDrawer = useCallback(() => setDrawerOpen(true), []);
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
        removeTask={upload.removeTask}
      />
      <BotSessionFilesModal
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        sessionName={session?.title || bot?.displayName || '当前会话'}
        readyFiles={files.readyFiles}
        isLoadingList={files.isLoadingList}
        botId={botId}
        sessionId={sessionId}
        userId={userId}
        ownerId={ownerId}
        onUploadClick={() => setUploadOpen(true)}
        onOpen={() => void files.refresh()}
        onDelete={(f) => void files.deleteFile(f)}
        onDownload={(f) => void files.downloadFile(f)}
        onReference={handleReference}
      />
    </>
  );

  return { senderRef, fileChip, command, fileToolbar, featureNode, openFileDrawer, openUpload };
}
