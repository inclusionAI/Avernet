import BotEngineVersionDialogs from '@/components/BotWorkshop/BotEngineVersionDialogs';
import { buildChatEngineVersionTarget } from '@/domain/chatEngineVersionTarget';
import { useBotEngineVersions } from '@/hooks/useBotEngineVersions';
import { botEngineVersionService } from '@/services/botWorkshop/botEngineVersionService';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useEffect, useMemo, useState } from 'react';
import { ChatEngineVersionNotice } from '../components/ChatEngineVersionNotice';

export function useChatEngineVersionNotice(input: {
  bot: ChatBotView | null;
  section: 'managed' | 'team' | 'friend' | null;
  origin: 'mine' | 'others';
  sessionId: string | null;
  userId: string | null;
}) {
  const { bot, section, origin, sessionId, userId } = input;
  const target = useMemo(
    () => buildChatEngineVersionTarget({ bot, section, origin, sessionId, userId }),
    [bot, section, origin, sessionId, userId],
  );
  const targets = useMemo(() => (target ? [target] : []), [target]);
  const versions = useBotEngineVersions(targets);
  const state = target ? versions.states[target.key] : undefined;
  const permission = target
    ? botEngineVersionService.getRestartPermission({ ...target.restartPermission,
        backendObservation: state && (state.kind === 'ready' || state.kind === 'updating') ? state.observation : undefined,
      })
    : { allowed: false, disabledReason: undefined };
  const [dismissedKey, setDismissedKey] = useState<string>();
  const [infoOpen, setInfoOpen] = useState(false);
  const [updateOpen, setUpdateOpen] = useState(false);

  useEffect(() => {
    setDismissedKey(undefined);
    setInfoOpen(false);
    setUpdateOpen(false);
  }, [target?.key]);

  const notice =
    state && target && target.key !== dismissedKey ? (
      <ChatEngineVersionNotice
        state={state}
        // 与 Bot 管理列表的「引擎有更新」一致：点击直达更新确认弹窗（dmore image 19），
        // 而不是引擎信息弹窗。
        onOpen={() => {
          setInfoOpen(false);
          setUpdateOpen(true);
        }}
        onDismiss={() => setDismissedKey(target.key)}
      />
    ) : null;
  const dialogs =
    target && state && bot ? (
      <BotEngineVersionDialogs
        botName={bot.displayName}
        state={state}
        infoOpen={infoOpen}
        updateOpen={updateOpen}
        canRestart={permission.allowed}
        updateDisabledReason={permission.disabledReason}
        onCloseInfo={() => setInfoOpen(false)}
        onCloseUpdate={() => setUpdateOpen(false)}
        onSelectInstance={(deviceUuid) => void versions.selectInstance(target.key, deviceUuid)}
        onRequestUpdate={() => setUpdateOpen(true)}
        onConfirmUpdate={(onRestartSubmitted) => versions.update(target.key, onRestartSubmitted)}
      />
    ) : null;

  return { notice, dialogs };
}
