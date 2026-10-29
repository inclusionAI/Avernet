import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import type { SendFileRefs } from '@/pages/Workspace/hooks/useBotChat';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { buildDebugChatFileRequest } from '@/services/botWorkshop/debugChatFiles';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { SESSION_FILE_ALLOWED_EXT } from '@/services/workspace/sessionFileUtils';
import { FileUp, Loader2, Paperclip, Send, Square, X } from 'lucide-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';

interface DebugChatComposerProps {
  botId: string;
  sessionId: string | null;
  userId: string | null;
  ownerId?: string;
  runtimeStage?: BotRuntimeStage;
  isRequesting: boolean;
  /** 会话模型选择器放在发送按钮左侧，保持调试输入区操作聚合。 */
  modelSelector?: ReactNode;
  capabilitySets?: ComposerCapabilitySetsState | null;
  onSend: (content: string, options?: SendFileRefs) => void;
  onStop: () => void;
}

export function DebugChatComposer({
  botId,
  sessionId,
  userId,
  ownerId,
  runtimeStage,
  isRequesting,
  modelSelector,
  capabilitySets,
  onSend,
  onStop,
}: DebugChatComposerProps) {
  const [draft, setDraft] = useState('');
  const [files, setFiles] = useState<BotSessionFileView[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const upload = useBotSessionFileUpload(botId, sessionId, userId, ownerId, runtimeStage);
  const cancelAllRef = useRef(upload.cancelAll);
  cancelAllRef.current = upload.cancelAll;

  useEffect(
    () => () => {
      cancelAllRef.current();
    },
    [],
  );

  const addReadyFile = (file: BotSessionFileView) => {
    setFiles((current) => [file, ...current.filter((item) => item.resourceId !== file.resourceId)]);
  };

  const chooseFiles = async (selected: File[]) => {
    if (selected.length === 0) return;
    upload.stageFiles(selected);
    await upload.submit(addReadyFile);
    upload.cancelAll();
  };

  const send = () => {
    if (isRequesting || (!draft.trim() && files.length === 0)) return;
    const request = buildDebugChatFileRequest(draft.trim(), files);
    onSend(request.content, request.options);
    setDraft('');
    setFiles([]);
  };

  return (
    <div className="px-5 py-4">
      {files.length > 0 || upload.tasks.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-2" aria-label="调试会话附件">
          {files.map((file) => (
            <span
              key={file.resourceId}
              className="inline-flex max-w-56 items-center gap-1.5 rounded-lg border border-border bg-muted px-2 py-1 text-xs"
            >
              <Paperclip className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="truncate">{file.displayName}</span>
              <Button
                variant="ghost"
                size="icon"
                className="size-5 rounded-md"
                aria-label={`移除附件 ${file.displayName}`}
                leftIcon={<X className="size-3" />}
                onClick={() => setFiles((current) => current.filter((item) => item.resourceId !== file.resourceId))}
              />
            </span>
          ))}
          {upload.tasks.map((task) => (
            <span
              key={task.localId}
              className="inline-flex max-w-56 items-center gap-1.5 rounded-lg border border-border bg-muted px-2 py-1 text-xs text-muted-foreground"
            >
              {task.phase === 'failed' ? (
                <FileUp className="size-3.5 shrink-0 text-destructive" aria-hidden />
              ) : (
                <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
              )}
              <span className="truncate">{task.name}</span>
              <span>{task.phase === 'failed' ? '失败' : `${task.progress}%`}</span>
            </span>
          ))}
        </div>
      ) : null}
      <Card className="rounded-2xl p-3 shadow-none">
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
        <Textarea
          value={draft}
          disabled={!sessionId}
          rows={2}
          placeholder="输入调试消息，Enter 发送"
          className="min-h-16 max-h-40 resize-none border-0 px-1 py-2 shadow-none focus-visible:ring-0"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
        />
        <div className="mt-2 flex items-center gap-2">
          <ComposerCapabilitiesMenu
            onUpload={() => fileInputRef.current?.click()}
            disabled={!sessionId || !userId || upload.isUploading || isRequesting}
            selections={[]}
            capabilitySets={capabilitySets}
            showTaskEntries={false}
          />
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {modelSelector}
            <Button
              size="icon"
              disabled={!sessionId || upload.isUploading || (!isRequesting && !draft.trim() && files.length === 0)}
              aria-label={isRequesting ? '停止生成' : '发送'}
              leftIcon={isRequesting ? <Square className="size-4" /> : <Send className="size-4" />}
              onClick={isRequesting ? onStop : send}
            />
          </div>
        </div>
      </Card>
    </div>
  );
}
