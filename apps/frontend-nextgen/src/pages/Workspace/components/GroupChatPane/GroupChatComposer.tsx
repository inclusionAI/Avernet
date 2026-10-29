import { FileIcon } from 'lucide-react';

import { MessageEditBar, MessageQuoteBar } from '@/components/Workspace/MessageInteractionToolbar';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { SessionView } from '@/domain/collaboration';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import { useGroupChatImageUpload } from '@/pages/Workspace/hooks/useGroupChatImageUpload';
import { buildQuotePrompt, type MessageQuote } from '@/pages/Workspace/hooks/useMessageInteractions';
import { useSessionFileUpload } from '@/pages/Workspace/hooks/useSessionFileUpload';
import { buildTaskInstructions } from '@/services/tasks/taskMapper';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import { cn } from '@/utils/cn';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { MentionConfig, SubmitContext } from '@tc-chat/ui';
import { Sender, type SenderRef } from '@tc-chat/ui/es/Sender';
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { GroupImagePreview } from './GroupImagePreview';
import { expandMentionIds } from './mentionHelpers';
import { UploadFilesModal } from './UploadFilesModal';

interface GroupChatComposerProps {
  session: SessionView | null;
  /** 连接状态：断连四态输入卡灰底不可输入（附录 B；视觉依赖 Sender disabled 形态=灰底）。 */
  connectionStatus: ProviderConnectionStatus;
  mentionConfig: MentionConfig | undefined;
  onSend: (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => void;
  /** 受控输入草稿（由 GroupChatPane 持有，便于副屏 onAction(fill_input) 回填）。 */
  draft: string;
  onDraftChange: (value: string) => void;
  /**
   * 主屏输入框 ref（绑定内部 <Sender ref>）。透传自 GroupChatPane.useGroupChat.inputRef,
   * 经 useChatBridge 注册到全局桥,使 aixcore 卡片 bridge.getInputRef().insert(text) 生效。"insert" 场景。
   */
  inputRef?: RefObject<SenderRef>;
  /** 任务发起能力（useTaskExecution），用于渲染能力菜单中的任务项。缺失则菜单不显示任务项。 */
  execution?: UseTaskExecutionResult | null;
  /** 当前消息区引用草稿；仅消费现有发送正文，不改变后端请求合同。 */
  quote?: MessageQuote | null;
  onClearQuote?: () => void;
  editingMessageId?: string | null;
  onCancelEdit?: () => void;
}

function ChatFileUploadModal({
  sessionId,
  open,
  onClose,
}: {
  sessionId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const upload = useSessionFileUpload(sessionId, () => {});

  return (
    <UploadFilesModal
      open={open}
      onClose={onClose}
      queue={upload.queue}
      stageFiles={upload.stageFiles}
      submitStaged={upload.submitStaged}
      cancelTask={upload.cancelTask}
      retryTask={upload.retryTask}
      discardAll={upload.discardAll}
      clearCompleted={upload.clearCompleted}
      hasPending={upload.hasPending}
    />
  );
}

export function GroupChatComposer({
  session,
  connectionStatus,
  mentionConfig,
  onSend,
  draft,
  onDraftChange,
  inputRef,
  execution,
  quote,
  onClearQuote,
  editingMessageId,
  onCancelEdit,
}: GroupChatComposerProps) {
  const [uploadFilesOpen, setUploadFilesOpen] = useState(false);
  const images = useGroupChatImageUpload(session?.sessionId);
  const imageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    onDraftChange('');
    setUploadFilesOpen(false);
  }, [session?.sessionId]);

  const triggerImagePicker = useCallback(() => {
    imageInputRef.current?.click();
  }, []);

  const handleImageInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      if (files.length > 0) images.addFiles(files);
      event.target.value = '';
    },
    [images],
  );

  const handlePasteFiles = useCallback(
    (files: File[]) => {
      const imageFiles = files.filter((file) => file.type.startsWith('image/'));
      if (imageFiles.length > 0) images.addFiles(imageFiles);
    },
    [images],
  );

  const fileChip = useMemo(
    () => ({
      label: '文件',
      icon: <FileIcon className="h-4 w-4" />,
      onPick: () => setUploadFilesOpen(true),
      onRemove: () => {},
    }),
    [],
  );

  const submit = useCallback(
    async (content: string, context?: SubmitContext) => {
      const hasText = content.trim().length > 0;
      const hasImages = images.images.length > 0;
      // 输出中不阻塞发送：消息进入后端投递队列依次消费（拥塞控制归属后端）。
      if ((!hasText && !hasImages) || images.isProcessing || images.isUploading) return;
      const quotedContent = quote ? `${buildQuotePrompt(quote.senderName, quote.text)}\n\n${content}` : content;
      // 任务胶囊选中态：逐胶囊构造任务指令消息发到群聊，由 bot/skill 解析触发（每胶囊各发起一个任务）。
      if (execution && execution.selections.length > 0) {
        for (const instruction of buildTaskInstructions(quotedContent, execution.selections)) {
          onSend(instruction, [], undefined);
        }
        execution.clearSelection();
        onDraftChange('');
        return;
      }
      const mentions = expandMentionIds(context?.mentions ?? [], session?.participants ?? []);
      let attachments: SessionMessageAttachment[] | undefined;
      if (images.images.length > 0 && session?.sessionId) {
        attachments = await images.uploadAll();
        images.clear();
      }
      onSend(quotedContent, mentions, attachments && attachments.length > 0 ? attachments : undefined);
    },
    [images, onSend, session?.participants, session?.sessionId, execution, onDraftChange, quote],
  );

  const submitImageOnlyOnEnter = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== 'Enter' || event.shiftKey || draft.trim() || images.images.length === 0) return;
      if (images.isProcessing || images.isUploading) return;
      event.preventDefault();
      void submit('', { mentions: [], commands: [] });
    },
    [draft, images.images.length, images.isProcessing, images.isUploading, submit],
  );

  return (
    // 横向/纵向 padding 由 index 的 composer 容器统一持有（帽条需以卡片实际宽度为基准每侧窄
    // 13px 并紧贴卡片顶，本根节点不得再自带 px/py 形成双重 inset / 顶缝）。
    <div className="flex w-full shrink-0 flex-col gap-2 bg-background">
      {editingMessageId ? <MessageEditBar onCancel={onCancelEdit ?? (() => {})} /> : null}
      <MessageQuoteBar quote={quote ?? null} onClear={onClearQuote ?? (() => {})} />
      <Sender
        ref={inputRef}
        // 断连四态灰卡（附录 B #F8F8F8≈muted）：SDK InputWrapper 底色吃 --aix-color-bg，
        // 在此 CSS 变量层注入实现灰化（disabled 仅灰化文字，不动 SDK 本体）。
        className={cn('w-full', session && connectionStatus !== 'connected' && '[--aix-color-bg:hsl(var(--muted))]')}
        value={draft}
        onChange={onDraftChange}
        onSubmit={(content, context) => {
          void submit(content, context);
        }}
        onKeyDown={submitImageOnlyOnEnter}
        onPasteFile={handlePasteFiles}
        loading={images.isProcessing || images.isUploading}
        disabled={!session || connectionStatus !== 'connected'}
        submitType="enter"
        mention={mentionConfig}
        fileChip={fileChip}
        // 群聊不提供 /file /link /skill 指令；占位仅说明发送身份。
        placeholder={session ? '输入消息... (将以您的用户身份发送到群内)' : '请选择一个会话'}
        slots={{
          header:
            images.images.length > 0 ? (
              <GroupImagePreview
                images={images.images}
                uploadStates={images.uploadStates}
                maxCount={9}
                onRemove={images.removeImage}
              />
            ) : undefined,
        }}
        imageUpload={{
          // 图片选择继续由「+ → 添加图片」统一提供；粘贴由 onPasteFile 单独接入，避免 SDK 重复渲染按钮。
          enabled: false,
          images: images.images,
          onAdd: images.addFiles,
          onRemove: images.removeImage,
        }}
        toolbar={{
          left: (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              {execution ? (
                <ComposerCapabilitiesMenu
                  execution={execution}
                  onUpload={() => setUploadFilesOpen(true)}
                  onAddImage={triggerImagePicker}
                  enableWorkflow={false}
                  disabled={!session}
                  selections={execution.selections}
                  onWorkflowSelected={execution.selectWorkflow}
                  onDynamicSelected={execution.selectDynamic}
                  onRemoveSelection={execution.removeSelection}
                />
              ) : null}
            </span>
          ),
          right: undefined,
        }}
      />
      <input
        ref={imageInputRef}
        type="file"
        accept="image/jpeg,image/jpg,image/png,image/webp,image/gif"
        multiple
        onChange={handleImageInputChange}
        className="hidden"
      />
      <ChatFileUploadModal
        sessionId={session?.sessionId ?? null}
        open={uploadFilesOpen}
        onClose={() => setUploadFilesOpen(false)}
      />
    </div>
  );
}
