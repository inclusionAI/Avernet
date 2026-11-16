import { DebugChatComposer } from '@/components/BotWorkshop/Editor/DebugChatComposer';
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { resolveChatRuntimeStage } from '@/services/workspace/botSessionService';
import type { ComponentProps } from 'react';
import { useEffect, useRef } from 'react';

type Props = Pick<ComponentProps<typeof DebugChatComposer>,
  'bot' | 'session' | 'userId' | 'isRequesting' | 'modelSelector' | 'onSend' | 'onStop'
> & { onClear: () => Promise<void> };

/** Panel keys this entire input scope, including the release upload/file overlays. */
export function DebugChatInput({ onClear, ...props }: Props) {
  const files = useBotSessionFilesFeature(
    props.bot, props.session, props.userId, onClear,
    props.bot.botType === 'service' ? resolveChatRuntimeStage(props.bot) : undefined,
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
        fileChip={files.fileChip}
        senderRef={files.senderRef}
        onUpload={files.openUpload}
      />
      {files.featureNode}
    </>
  );
}
