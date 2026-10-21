/** @jest-environment jsdom */
import { BotEngineVersionMenuItems } from '@/components/BotWorkshop/BotCard/BotEngineVersionMenuItems';
import { useBotTableEngineVersions } from '@/hooks/useBotTableEngineVersions';
import { botEngineVersionService } from '@/services/botWorkshop/botEngineVersionService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { render, renderHook, waitFor } from '@testing-library/react';

const observation = {
  detection: { status: 'known' as const },
  current: { raw_version: '1.0' },
  release_target: { status: 'available' as const, release_id: 'REL-next' },
  comparison: 'older' as const,
  restart_hint: 'recommended' as const,
  context: { bot_id: 'cloud', stage: 'draft', checked_at: '2026-10-21T00:00:00Z' },
};

afterEach(() => jest.restoreAllMocks());

test.each(['openclaw', 'hermes'].flatMap((engine) => ['desktop', 'DESKTOP', ' Desktop '].map((botType) => [engine, botType])))('skips desktop %s inspection for %s while still inspecting cloud Bots', async (engine, botType) => {
  const desktop = mapBotDto({
    bot_id: 'desktop', bot_name: 'Desktop', bot_type: botType,
    engine, status: 'ACTIVE', owner_entity_id: 'owner', actions: ['restart'],
  }).item;
  const cloud = mapBotDto({
    bot_id: 'cloud', bot_name: 'Cloud', bot_type: 'personal',
    engine, status: 'ACTIVE', owner_entity_id: 'owner', actions: ['restart'],
  }).item;
  const inspect = jest.spyOn(botEngineVersionService, 'inspect').mockResolvedValue({ kind: 'ready', observation });
  const { result } = renderHook(() => useBotTableEngineVersions({ bots: [desktop, cloud], userId: 'caller' }));
  await waitFor(() => expect(Object.values(result.current.states).some((state) => state?.kind === 'ready')).toBe(true));
  expect(inspect).toHaveBeenCalledTimes(1);
  expect(inspect).toHaveBeenCalledWith(expect.objectContaining({ botId: 'cloud' }));
  expect(result.current.states[desktop.entityKey]?.kind).toBe('idle');
});

test.each(['openclaw', 'hermes'].flatMap((engine) => ['desktop', 'DESKTOP', ' Desktop '].map((botType) => [engine, botType])))('hides desktop %s engine version menu for %s even with update availability', (engine, botType) => {
  const desktop = mapBotDto({
    bot_id: 'desktop', bot_name: 'Desktop', bot_type: botType,
    engine, status: 'ACTIVE', owner_entity_id: 'owner', actions: ['restart'],
  }).item;
  const { container } = render(<BotEngineVersionMenuItems
    bot={desktop} availability={{ enabled: true }} onOpenInfo={jest.fn()} onUpdate={jest.fn()}
  />);
  expect(container.childElementCount).toBe(0);
});
