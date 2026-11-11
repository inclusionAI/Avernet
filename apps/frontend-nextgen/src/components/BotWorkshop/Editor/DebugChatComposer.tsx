import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { SendFileRefs } from '@/pages/Workspace/hooks/useBotChat';
import { Sender } from '@tc-chat/ui/es/Sender';
import type { CommandConfig, FileChipConfig, SenderRef, SubmitContext } from '@tc-chat/ui/es/Sender';
import type { ReactNode, Ref, RefObject } from 'react';
import { useState } from 'react';

interface DebugChatComposerProps {
  sessionId: string | null;
  userId: string | null;
  isRequesting: boolean;
  /** 会话模型选择器放在发送按钮左侧，保持调试输入区操作聚合。 */
  modelSelector?: ReactNode;
  capabilitySets?: ComposerCapabilitySetsState | null;
  command?: CommandConfig;
  fileChip?: FileChipConfig;
  senderRef?: RefObject<SenderRef | null>;
  onUpload?: () => void;
  onSend: (content: string, options?: SendFileRefs) => void;
  onStop: () => void;
}

function toFileOptions(context: SubmitContext): SendFileRefs | undefined {
  if (!context.fileRefs?.length) return undefined;
  return {
    resourceReferences: context.fileRefs.map((file) => ({
      type: 'file',
      resource_id: file.resource_id,
      insert_id: file.insert_id,
    })),
    promptFileRefs: context.fileRefs.map((file) => ({
      resource_id: file.resource_id,
      insert_id: file.insert_id,
    })),
    fileRefDisplay: context.fileRefs.map((file) => ({
      insert_id: file.insert_id,
      name: file.display_name,
    })),
  };
}

export function DebugChatComposer({
  sessionId,
  userId,
  isRequesting,
  modelSelector,
  capabilitySets,
  command,
  fileChip,
  senderRef,
  onUpload,
  onSend,
  onStop,
}: DebugChatComposerProps) {
  const [draft, setDraft] = useState('');

  const send = (message: string, context: SubmitContext) => {
    const content = context.resolvedContent ?? message;
    if (isRequesting || !content.trim()) return;
    onSend(content, toFileOptions(context));
    setDraft('');
  };

  return (
    <div className="px-5 py-4">
      <Sender
        ref={senderRef as Ref<SenderRef>}
        className="sender-shell w-full"
        value={draft}
        disabled={!sessionId || !userId || isRequesting}
        loading={isRequesting}
        submitType="enter"
        placeholder="可使用 /file 引用文件、/skill 调用能力、/clear 清空上下文"
        command={command}
        fileChip={fileChip}
        onChange={setDraft}
        onSubmit={send}
        onCancel={onStop}
        toolbar={{
          left: (
            <ComposerCapabilitiesMenu
              onUpload={onUpload}
              disabled={!sessionId || !userId || isRequesting}
              selections={[]}
              capabilitySets={capabilitySets}
              showTaskEntries={false}
            />
          ),
          right: modelSelector,
        }}
      />
    </div>
  );
}
