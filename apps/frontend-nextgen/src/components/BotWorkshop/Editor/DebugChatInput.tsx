import { DebugChatComposer } from '@/components/BotWorkshop/Editor/DebugChatComposer';
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { resolveChatRuntimeStage } from '@/services/workspace/botSessionService';
import type { ComponentProps } from 'react';
import { useEffect, useRef } from 'react';

type Props = Pick<ComponentProps<typeof DebugChatComposer>,
  'bot' | 'session' | 'userId' | 'isRequesting' | 'modelSelector' | 'composerDisabled'
  | 'composerDisabledPlaceholder' | 'onSend' | 'onStop'
> & { onClear: () => Promise<void> };

/** Panel keys this entire input scope, including the release upload/file overlays. */
export function DebugChatInput({ onClear, ...props }: Props) {
  const files = useBotSessionFilesFeature(
    props.bot, props.session, props.userId, onClear,
    props.bot.botType === 'service' ? resolveChatRuntimeStage(props.bot) : undefined,
    { subjectScoped: false },
  );
  const cancelUploads = useRef(files.cancelUploads);
  cancelUploads.current = files.cancelUploads;
  useEffect(() => () => cancelUploads.current(), []);

  return (
    <>
      <DebugChatComposer
        {...props}
        capabilitySets={files.capabilitySets}
        command={files.command}
        fileChip={files.filesSupported ? files.fileChip : undefined}
        senderRef={files.senderRef}
        onUpload={files.filesSupported ? files.openUpload : undefined}
      />
      {files.featureNode}
    </>
  );
}
