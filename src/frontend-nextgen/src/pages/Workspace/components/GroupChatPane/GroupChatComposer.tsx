import { RefreshCw } from 'lucide-react';

import { Badge } from '@/components/ui';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { SessionView } from '@/domain/collaboration';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import { useGroupChatImageUpload } from '@/pages/Workspace/hooks/useGroupChatImageUpload';
import { useSessionFileUpload } from '@/pages/Workspace/hooks/useSessionFileUpload';
import { buildTaskInstruction } from '@/services/tasks/taskMapper';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { MentionConfig, SubmitContext } from '@tc-chat/ui';
import { Sender, ToolbarButton, type SenderRef } from '@tc-chat/ui/es/Sender';
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { toast } from 'sonner';
import { GroupImagePreview } from './GroupImagePreview';
import { expandMentionIds } from './mentionHelpers';
import { UploadFilesModal } from './UploadFilesModal';

interface GroupChatComposerProps {
  session: SessionView | null;
  isRequesting: boolean;
  connectionStatus: ProviderConnectionStatus;
  mentionConfig: MentionConfig | undefined;
  showReconnectToolbar: boolean;
  onSend: (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => void;
  onStop: () => void;
  onReconnect: () => void;
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
}

function ChatFileUploadModal({
  sessionId,
  open,
  onClose,
  onUploaded,
}: {
  sessionId: string | null;
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const upload = useSessionFileUpload(sessionId, () => {
    onUploaded();
  });

  return (
    <UploadFilesModal
      open={open}
      onClose={onClose}
      queue={upload.queue}
      isUploading={upload.isUploading}
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
  isRequesting,
  connectionStatus,
  mentionConfig,
  showReconnectToolbar,
  onSend,
  onStop,
  onReconnect,
  draft,
  onDraftChange,
  inputRef,
  execution,
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

  const submit = useCallback(
    async (content: string, context?: SubmitContext) => {
      const hasText = content.trim().length > 0;
      const hasImages = images.images.length > 0;
      if ((!hasText && !hasImages) || isRequesting || images.isProcessing || images.isUploading) return;
      // 任务选中态：构造 /task 指令消息发到群聊，由 bot/skill 解析触发任务。
      if (execution && (execution.selectedWorkflow || execution.pendingDynamic)) {
        onSend(buildTaskInstruction(content, execution.selectedWorkflow, execution.pendingDynamic), [], undefined);
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
      onSend(content, mentions, attachments && attachments.length > 0 ? attachments : undefined);
    },
    [images, isRequesting, onSend, session?.participants, session?.sessionId, execution, onDraftChange],
  );

  const cancel = useCallback(() => {
    if (images.isUploading || images.isProcessing) return;
    if (isRequesting) onStop();
  }, [images.isProcessing, images.isUploading, isRequesting, onStop]);

  const submitImageOnlyOnEnter = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== 'Enter' || event.shiftKey || draft.trim() || images.images.length === 0) return;
      if (isRequesting || images.isProcessing || images.isUploading) return;
      event.preventDefault();
      void submit('', { mentions: [], commands: [] });
    },
    [draft, images.images.length, images.isProcessing, images.isUploading, isRequesting, submit],
  );

  return (
    <div className="border-t border-[var(--color-border)] bg-white px-6 py-4">
      <Sender
        ref={inputRef}
        value={draft}
        onChange={onDraftChange}
        onSubmit={(content, context) => {
          void submit(content, context);
        }}
        onKeyDown={submitImageOnlyOnEnter}
        onCancel={cancel}
        loading={isRequesting || images.isProcessing || images.isUploading}
        disabled={!session || isRequesting}
        submitType="enter"
        mention={mentionConfig}
        placeholder={session ? `在 ${session.title} 中发送消息…` : '请选择一个会话'}
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
          enabled: false,
          images: images.images,
          onAdd: images.addFiles,
          onRemove: images.removeImage,
        }}
        toolbar={{
          left: (
            <span className="flex items-center gap-1.5 text-xs text-[var(--color-muted)]">
              {showReconnectToolbar ? (
                <>
                  <Badge tone={connectionStatus === 'reconnecting' ? 'warning' : 'neutral'}>
                    {connectionStatus === 'reconnecting' ? '重连中' : '已断开'}
                  </Badge>
                  <ToolbarButton
                    label="点击重新连接"
                    icon={<RefreshCw className="h-3.5 w-3.5" />}
                    onClick={() => {
                      void onReconnect();
                    }}
                  />
                </>
              ) : null}
              {execution ? (
                <ComposerCapabilitiesMenu
                  execution={execution}
                  onUpload={() => setUploadFilesOpen(true)}
                  onAddImage={triggerImagePicker}
                  enableWorkflow={false}
                  disabled={!session || isRequesting}
                  selectedWorkflow={execution.selectedWorkflow}
                  pendingDynamic={execution.pendingDynamic}
                  onWorkflowSelected={execution.selectWorkflow}
                  onDynamicSelected={execution.selectDynamic}
                  onClearSelection={execution.clearSelection}
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
        onUploaded={() => {
          toast.success('文件上传完成');
        }}
      />
    </div>
  );
}
