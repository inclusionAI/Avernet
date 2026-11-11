import { Input } from '@/components/ui/Input';
import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import { ConversationComposerHost } from '@/components/Workspace/ConversationComposerHost';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { SendFileRefs } from '@/pages/Workspace/hooks/useBotChat';
import { useBotSessionCommands } from '@/pages/Workspace/hooks/useBotSessionCommands';
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import { buildDebugChatFileRequest } from '@/services/botWorkshop/debugChatFiles';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import {
  resolveChatRuntimeStage,
  type BotChatSessionView,
  type ChatBotView,
} from '@/services/workspace/botSessionService';
import { SESSION_FILE_ALLOWED_EXT } from '@/services/workspace/sessionFileUtils';
import { Sender, type FileChipConfig, type SenderRef, type SubmitContext } from '@tc-chat/ui/es/Sender';
import { FileIcon, FileUp, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

interface DebugChatComposerProps {
  bot: ChatBotView;
  session: BotChatSessionView | null;
  userId: string | null;
  isRequesting: boolean;
  modelSelector?: ReactNode;
  capabilitySets?: ComposerCapabilitySetsState | null;
  onClear: () => Promise<void>;
  onSend: (content: string, options?: SendFileRefs) => void;
  onStop: () => void;
}

export function DebugChatComposer({
  bot,
  session,
  userId,
  isRequesting,
  modelSelector,
  capabilitySets,
  onClear,
  onSend,
  onStop,
}: DebugChatComposerProps) {
  const [draft, setDraft] = useState('');
  const senderRef = useRef<SenderRef | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sessionId = session?.sessionId ?? null;
  const runtimeStage = resolveChatRuntimeStage(bot);
  const files = useBotSessionFiles(bot.realBotId, sessionId, userId, bot.ownerId, undefined, runtimeStage);
  const skillsState = useBotSkills(bot, userId, false);
  const upload = useBotSessionFileUpload(bot.realBotId, sessionId, userId, bot.ownerId, runtimeStage);
  const cancelAllRef = useRef(upload.cancelAll);
  cancelAllRef.current = upload.cancelAll;
  const alive = useRef(true);
  const enabled = !!sessionId && !!userId;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      cancelAllRef.current();
    };
  }, []);

  const referenceFile = useCallback((file: BotSessionFileView) => {
    if (!alive.current || file.status !== 'ready') return;
    senderRef.current?.insertFileChips([{ resource_id: file.resourceId, display_name: file.displayName }]);
    senderRef.current?.focus();
  }, []);
  const focusInput = useCallback(() => senderRef.current?.focus(), []);
  const command = useBotSessionCommands({
    enabled,
    canUseSkills: !bot.isFriendBot,
    files,
    skillsState,
    handleReference: referenceFile,
    handleSelectSkill: focusInput,
    onClear,
  });
  const fileChip = useMemo<FileChipConfig>(
    () => ({
      label: '文件',
      icon: <FileIcon className="size-4" />,
      onPick: () => fileInputRef.current?.click(),
    }),
    [],
  );

  const chooseFiles = async (selected: File[]) => {
    if (!enabled || isRequesting || selected.length === 0) return;
    upload.stageFiles(selected);
    await upload.submit(referenceFile);
    if (alive.current) upload.cancelAll();
  };
  const send = (content: string, context?: SubmitContext) => {
    if (!enabled || isRequesting || upload.isUploading || !content.trim()) return;
    const request = buildDebugChatFileRequest(content, context);
    onSend(request.content, request.options);
  };

  return (
    <div className="px-5 py-4">
      <Input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        aria-label="选择调试会话文件"
        accept={SESSION_FILE_ALLOWED_EXT.map((extension) => `.${extension}`).join(',')}
        onChange={(event) => {
          void chooseFiles(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />
      <ConversationComposerHost
        bot={bot}
        session={session}
        command={command}
        onSend={send}
        onInvokeCommand={(token) => {
          const sender = senderRef.current;
          if (!sender) return;
          const current = sender.getValue();
          sender.insert(`${current && !/\s$/.test(current) ? ' ' : ''}${token}`);
          sender.focus();
        }}
      >
        {(composer) => (
          <Sender
            ref={senderRef}
            className="sender-shell w-full"
            value={draft}
            onChange={setDraft}
            onSubmit={composer.onSend}
            onCancel={onStop}
            loading={isRequesting}
            disabled={!enabled || upload.isUploading}
            submitType="enter"
            placeholder={composer.placeholder ?? '可使用 /file 引用文件、/skill 调用能力'}
            command={composer.command}
            fileChip={fileChip}
            slots={{
              header:
                composer.header || upload.tasks.length > 0 ? (
                  <>
                    {composer.header}
                    {upload.tasks.length > 0 ? (
                      <div className="flex flex-wrap gap-2 px-3 py-2" aria-label="调试会话上传状态">
                        {upload.tasks.map((task) => (
                          <span
                            key={task.localId}
                            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
                          >
                            {task.phase === 'failed' ? (
                              <FileUp className="size-3.5 text-destructive" />
                            ) : (
                              <Loader2 className="size-3.5 animate-spin" />
                            )}
                            <span>{task.name}</span>
                            <span>{task.phase === 'failed' ? '失败' : `${task.progress}%`}</span>
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </>
                ) : undefined,
            }}
            toolbar={{
              left: (
                <ComposerCapabilitiesMenu
                  onUpload={() => fileInputRef.current?.click()}
                  disabled={!enabled || upload.isUploading || isRequesting}
                  selections={[]}
                  capabilitySets={capabilitySets}
                  showTaskEntries={false}
                  extraMenuRows={composer.extraMenuRows}
                />
              ),
              right: modelSelector,
            }}
          />
        )}
      </ConversationComposerHost>
    </div>
  );
}
