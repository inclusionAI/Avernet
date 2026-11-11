import type { BotDomain } from '@/services/botWorkshop';
import { botManagementActionService } from '@/services/botWorkshop/botManagementActionService';
import { botWorkshopService } from '@/services/botWorkshop/botWorkshopService';

jest.mock('@/services/botWorkshop/botWorkshopService', () => ({
  botWorkshopService: {
    refetchEngine: jest.fn(),
    restart: jest.fn(),
    restartEngine: jest.fn(),
  },
}));

const codingBot = {
  id: 'coding-bot',
  deployment: 'cloud',
  runtime: { isAgentCodingBot: true },
} as unknown as BotDomain;
const openClawBot = {
  id: 'openclaw-bot',
  deployment: 'cloud',
  runtime: {},
} as unknown as BotDomain;
const localBot = {
  id: 'local-bot',
  deployment: 'local',
  runtime: {},
} as unknown as BotDomain;

beforeEach(() => jest.clearAllMocks());

test('Coding Bot 的引擎重启预设为 refetch 引擎接口', async () => {
  await botManagementActionService.run('engine_restart', codingBot);

  expect(botWorkshopService.refetchEngine).toHaveBeenCalledWith('coding-bot');
  expect(botWorkshopService.restart).not.toHaveBeenCalled();
  expect(botWorkshopService.restartEngine).not.toHaveBeenCalled();
});

test('普通云 Bot 继续使用 engine restart 接口', async () => {
  await botManagementActionService.run('engine_restart', openClawBot);

  expect(botWorkshopService.restartEngine).toHaveBeenCalledWith('openclaw-bot');
  expect(botWorkshopService.refetchEngine).not.toHaveBeenCalled();
});

test('普通本地 Bot 保持容器重启行为', async () => {
  await botManagementActionService.run('engine_restart', localBot);

  expect(botWorkshopService.restart).toHaveBeenCalledWith(localBot);
  expect(botWorkshopService.refetchEngine).not.toHaveBeenCalled();
});
