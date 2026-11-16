import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { mapPublication } from '@/services/botWorkshop/botEditorMappers';
import { canRetryPublication, type ServicePublication } from '@/domain/botEditor';
import type { ServicePublicationDto } from '@/services/backendApi/bots/botEditorController';

const published = mapBotDto({
  bot_id: 'service-bot',
  bot_name: 'Service Bot',
  bot_type: 'service',
  kind: 'service',
  engine: 'openclaw',
  owner_entity_id: 'owner-1',
}).item;

test('mapPublication keeps backend retry action while domain state is failed', () => {
  const result = mapPublication({
    publication_id: 1,
    version: 1,
    card_id: published.id,
    internal_status: 'failed',
    status: 'failed',
    available_actions: ['retry'],
  } as unknown as ServicePublicationDto);

  expect(result.status).toBe('failed');
  expect(result.internalStatus).toBe('failed');
  expect(result.availableActions).toEqual(['retry']);
});

test('mapPublication retains backend retry action for domain-side guarding', () => {
  const result = mapPublication({
    publication_id: 1,
    version: 1,
    card_id: published.id,
    internal_status: 'validating',
    status: 'validating',
    available_actions: ['retry'],
  } as unknown as ServicePublicationDto);

  expect(result.internalStatus).toBe('validating');
  expect(result.availableActions).toEqual(['retry']);
});

test('retry helper accepts valid failed status only', () => {
  const valid = mapPublication({
    publication_id: 1,
    version: 1,
    card_id: published.id,
    internal_status: 'failed',
    status: 'failed',
    available_actions: ['retry'],
  } as unknown as ServicePublicationDto);
  const invalid: ServicePublication = {
    ...valid,
    internalStatus: 'validating',
    availableActions: ['retry'],
  };

  expect(canRetryPublication(valid)).toBe(true);
  expect(canRetryPublication(invalid)).toBe(false);
});

test('valid failed status keeps retry available', () => {
  const valid = mapPublication({
    publication_id: 1,
    version: 1,
    card_id: published.id,
    internal_status: 'failed',
    status: 'deploying',
    available_actions: ['retry'],
  } as unknown as ServicePublicationDto);

  expect(valid.availableActions).toEqual(['retry']);
  expect(canRetryPublication(valid)).toBe(true);
});
