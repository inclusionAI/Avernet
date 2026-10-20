import BotEngineVersionDialogs from '@/components/BotWorkshop/BotEngineVersionDialogs';
import type { EngineVersionTarget } from '@/domain/botEngineVersion';
import { useBotEngineVersions } from '@/hooks/useBotEngineVersions';
import type { BotDomain } from '@/services/botWorkshop';
import { botEngineVersionService } from '@/services/botWorkshop/botEngineVersionService';
import { useMemo, useState } from 'react';

export function botTableEngineVersionKey(bot: BotDomain): string {
  return bot.cardId ?? bot.entityKey;
}

export function useBotTableEngineVersions(options: {
  bots: BotDomain[];
  userId?: string;
  onUpdated?: (bot: BotDomain) => Promise<void> | void;
}) {
  const { bots, userId, onUpdated } = options;
  const targets = useMemo<EngineVersionTarget[]>(
    () =>
      bots.map((bot) => ({
        key: botTableEngineVersionKey(bot),
        botId: bot.id,
        userId: userId ?? '',
        ownerId: bot.ownerId ?? '',
        engine: bot.runtime.engine,
        enabled: Boolean(userId && bot.ownerId) && bot.lifecycle !== 'deploying',
        restartPermission: botEngineVersionService.getRestartPermissionInput(bot),
      })),
    [bots, userId],
  );
  const versions = useBotEngineVersions(targets);
  const [selectedKey, setSelectedKey] = useState<string>();
  const [infoOpen, setInfoOpen] = useState(false);
  const [updateOpen, setUpdateOpen] = useState(false);
  const selectedBot = selectedKey ? bots.find((bot) => botTableEngineVersionKey(bot) === selectedKey) : undefined;
  const selectedState = selectedKey ? versions.states[selectedKey] : undefined;
  const updateAvailability = useMemo(
    () =>
      Object.fromEntries(
        bots.map((bot) => {
          const key = botTableEngineVersionKey(bot);
          return [
            key,
            botEngineVersionService.getUpdateAvailability(
              botEngineVersionService.getRestartPermissionInput(bot),
              versions.states[key],
            ),
          ];
        }),
      ),
    [bots, versions.states],
  );

  const openInfo = (bot: BotDomain) => {
    setSelectedKey(botTableEngineVersionKey(bot));
    setInfoOpen(true);
    setUpdateOpen(false);
  };
  const openUpdate = (bot: BotDomain) => {
    setSelectedKey(botTableEngineVersionKey(bot));
    setInfoOpen(false);
    setUpdateOpen(true);
  };
  const dialogs =
    selectedKey && selectedBot && selectedState ? (
      <BotEngineVersionDialogs
        botName={selectedBot.name}
        state={selectedState}
        infoOpen={infoOpen}
        updateOpen={updateOpen}
        canRestart={
          botEngineVersionService.getRestartPermission(botEngineVersionService.getRestartPermissionInput(selectedBot))
            .allowed
        }
        updateDisabledReason={
          botEngineVersionService.getRestartPermission(botEngineVersionService.getRestartPermissionInput(selectedBot))
            .disabledReason
        }
        onCloseInfo={() => setInfoOpen(false)}
        onCloseUpdate={() => setUpdateOpen(false)}
        onSelectInstance={(deviceUuid) => void versions.selectInstance(selectedKey, deviceUuid)}
        onRequestUpdate={() => setUpdateOpen(true)}
        onConfirmUpdate={async () => {
          const updated = await versions.update(selectedKey);
          if (updated) void Promise.resolve(onUpdated?.(selectedBot)).catch(() => undefined);
          return updated;
        }}
      />
    ) : null;

  return {
    states: versions.states,
    updateAvailability,
    openInfo,
    openUpdate,
    dialogs,
  };
}
