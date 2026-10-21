import { Button } from '@/components/ui/Button';
import { supportsEngineVersion, type EngineVersionUpdateAvailability } from '@/domain/botEngineVersion';
import type { BotDomain } from '@/domain/botWorkshop';
import { actionIcon } from './config';
import { ActionHelp, MenuSection } from './menuPrimitives';

interface BotEngineVersionMenuItemsProps {
  bot: BotDomain;
  availability?: EngineVersionUpdateAvailability;
  onOpenInfo?: (bot: BotDomain) => void;
  onUpdate?: (bot: BotDomain) => void;
}

export function BotEngineVersionMenuItems({ bot, availability, onOpenInfo, onUpdate }: BotEngineVersionMenuItemsProps) {
  if (bot.deployment === 'local' || !supportsEngineVersion(bot.runtime.engine) || (!onOpenInfo && !onUpdate)) return null;

  const canUpdate = availability?.enabled === true;
  const disabledReason = availability?.disabledReason ?? '当前版本状态不可确认';

  return (
    <MenuSection divider>
      {onOpenInfo ? (
        <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => onOpenInfo(bot)}>
          引擎信息
        </Button>
      ) : null}
      {onUpdate ? (
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start"
          leftIcon={actionIcon.engine_restart}
          disabled={!canUpdate}
          onClick={() => onUpdate(bot)}
        >
          {canUpdate ? (
            '更新引擎版本'
          ) : (
            <ActionHelp description={disabledReason ?? '当前不可用'}>更新引擎版本</ActionHelp>
          )}
        </Button>
      ) : null}
    </MenuSection>
  );
}
