import * as localApi from '@/services/backendApi/bots/localBotController';
import { localBotService, localCreateRequest } from '@/services/botWorkshop/localBotService';
jest.mock('@/services/backendApi/bots/localBotController');
const input = {
  scenario: 'local' as const,
  name: ' Desk ',
  description: '',
  engine: 'hermes',
  spaceId: '',
  ownership: 'personal' as const,
  serviceMode: 'non-service' as const,
  initialize: true,
  local: { machineId: 'm', mountPath: '/workspace/Desk' },
};
test('local request retains authorization parameters without cloud fields', () => {
  expect(localCreateRequest(input)).toEqual({
    bot_name: 'Desk',
    bot_desc: '',
    engine: 'hermes',
    machine_id: 'm',
    mount_path: '/workspace/Desk',
  });
});
test('unsupported engines and missing local paths are refused', () => {
  expect(() => localCreateRequest({ ...input, engine: 'claude_code' })).toThrow();
  expect(() => localCreateRequest({ ...input, local: undefined })).toThrow();
});

test('pending local authorization preserves machine and path through completion', async () => {
  jest
    .mocked(localApi.createLocalBot)
    .mockResolvedValue({ code: 200000, data: { bot_id: 'b', iframe_url: 'https://example.com/auth' } });
  const created = await localBotService.create(input);
  expect(created.type).toBe('authorization_required');
  if (created.type !== 'authorization_required') throw new Error('expected authorization');
  const request = localCreateRequest(input);
  expect(created.request).toEqual(request);
  jest.mocked(localApi.pollLocalAuthorization).mockResolvedValue({
    code: 200000,
    data: { status: 'ISSUED', bot: { bot_id: 'b', active_engine: 'hermes', status: 'PENDING' } },
  });
  const result = await localBotService.poll('b', request);
  expect(localApi.pollLocalAuthorization).toHaveBeenCalledWith('b', request);
  expect(result.bot?.deployment).toBe('local');
});
test('directory failure does not synthesize a local path', async () => {
  jest.mocked(localApi.getLocalDirectory).mockRejectedValue(new Error('device offline'));
  await expect(localBotService.directory('offline')).rejects.toThrow('device offline');
});
