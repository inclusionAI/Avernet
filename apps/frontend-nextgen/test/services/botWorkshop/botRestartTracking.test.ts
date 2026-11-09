/** @jest-environment jsdom */
import type { BotDomain } from '@/domain/botWorkshop';
import { getBot } from '@/services/backendApi/bots/botController';
import { mapBotDto } from '@/services/bots/botMapper';
import {
  CODING_RESTART_PIN_TTL_MS,
  applyRestartOutcome,
  needsFastInventorySync,
  readCodingRestartRecord,
  reconcileRestartPins,
  resolveExpiredRestartPin,
  withRestartResponse,
} from '@/services/botWorkshop/botRestartTracking';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';

jest.mock('@/services/backendApi/bots/botController', () => ({
  getBot: jest.fn(),
}));

const mockedGetBot = getBot as jest.MockedFunction<typeof getBot>;

function codingBot(): BotDomain {
  return mapBotDto({
    bot_id: 'coding-b1',
    owner_entity_id: '382641',
    bot_name: 'Coding Bot',
    active_engine: 'claude_code',
    bot_type: 'personal',
    status: 'ACTIVE',
    display_state: 'running',
    actions: ['view', 'restart'],
  }).item;
}

function syncBot(): BotDomain {
  return mapBotDto({
    bot_id: 'openclaw-b1',
    owner_entity_id: '382641',
    bot_name: 'OpenClaw Bot',
    engine: 'openclaw',
    bot_type: 'personal',
    status: 'ACTIVE',
    display_state: 'running',
    actions: ['view', 'restart'],
  }).item;
}

const codingRestartResponse = {
  status: 'ACTIVE',
  ext: {
    aicoding: {
      restart: { operation_id: 'op-1', status: 'RUNNING', phase: 'QUEUED', message: null },
    },
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  useBotWorkshopStore.getState().reset();
});

describe('withRestartResponse', () => {
  it('coding journal turns the card into a pinned restarting state', () => {
    const item = withRestartResponse(codingBot(), codingRestartResponse, 1_000);
    expect(item).toMatchObject({
      lifecycle: 'deploying',
      rawStatus: 'PENDING',
      rawDisplayState: undefined,
      pendingRestart: {
        operationId: 'op-1',
        startedAt: 1_000,
        deadlineAt: 1_000 + CODING_RESTART_PIN_TTL_MS,
        extensions: 0,
      },
    });
  });

  it('follows terminal coding journal outcomes instead of pinning them', () => {
    const failed = withRestartResponse(codingBot(), {
      status: 'ACTIVE',
      ext: {
        aicoding: {
          restart: { operation_id: 'op-1', status: 'FAILED', phase: 'QUEUED', message: '目标已变化' },
        },
      },
    });
    expect(failed).toMatchObject({ lifecycle: 'failed', rawStatus: 'FAILED' });
    expect(failed.pendingRestart).toBeUndefined();

    const succeeded = withRestartResponse(codingBot(), {
      status: 'ACTIVE',
      ext: {
        aicoding: {
          restart: { operation_id: 'op-1', status: 'SUCCEEDED', phase: 'SUCCEEDED', message: null },
        },
      },
    });
    expect(succeeded).toMatchObject({ lifecycle: 'running', rawStatus: 'ACTIVE' });
    expect(succeeded.pendingRestart).toBeUndefined();
  });

  it('synchronous engines adopt the status written by the mutation', () => {
    expect(withRestartResponse(syncBot(), { status: 'PENDING' })).toMatchObject({
      lifecycle: 'deploying',
      rawStatus: 'PENDING',
    });
    expect(withRestartResponse(syncBot(), { status: 'ACTIVE' }).lifecycle).toBe('running');
    expect(withRestartResponse(syncBot(), undefined).lifecycle).toBe('running');
  });
});

describe('reconcileRestartPins', () => {
  it('keeps the pin when the inventory still reports the stale ACTIVE value', () => {
    const pinned = withRestartResponse(codingBot(), codingRestartResponse, 1_000);
    const merged = reconcileRestartPins([pinned], [codingBot()]);
    expect(merged[0]).toMatchObject({ lifecycle: 'deploying', rawStatus: 'PENDING' });
    expect(merged[0].pendingRestart?.operationId).toBe('op-1');
    expect(needsFastInventorySync(merged[0])).toBe(true);
  });

  it('switches to server truth after PENDING and completes on the following ACTIVE', () => {
    const pinned = withRestartResponse(codingBot(), codingRestartResponse, 1_000);
    const pendingServer = mapBotDto({
      bot_id: 'coding-b1',
      owner_entity_id: '382641',
      active_engine: 'claude_code',
      bot_type: 'personal',
      status: 'PENDING',
      actions: ['view'],
    }).item;
    const progressing = reconcileRestartPins([pinned], [pendingServer]);
    expect(progressing[0].pendingRestart).toMatchObject({ operationId: 'op-1', sawProgress: true });

    const activeServer = codingBot();
    const completed = reconcileRestartPins(progressing, [activeServer]);
    expect(completed[0].lifecycle).toBe('running');
    expect(completed[0].pendingRestart).toBeUndefined();
    expect(needsFastInventorySync(completed[0])).toBe(false);
  });

  it('releases the pin when the server reports a terminal failure', () => {
    const pinned = withRestartResponse(codingBot(), codingRestartResponse, 1_000);
    const failedServer = mapBotDto({
      bot_id: 'coding-b1',
      owner_entity_id: '382641',
      active_engine: 'claude_code',
      bot_type: 'personal',
      status: 'FAILED',
      actions: ['view'],
    }).item;
    const merged = reconcileRestartPins([pinned], [failedServer]);
    expect(merged[0].pendingRestart).toBeUndefined();
    expect(merged[0].lifecycle).toBe('failed');
  });
});

describe('applyRestartOutcome', () => {
  it('patches only the matching card in the store', () => {
    useBotWorkshopStore.setState({ items: [codingBot(), syncBot()] });
    applyRestartOutcome(codingBot(), codingRestartResponse, 1_000);
    const [coding, openclaw] = useBotWorkshopStore.getState().items;
    expect(coding.pendingRestart?.operationId).toBe('op-1');
    expect(openclaw.pendingRestart).toBeUndefined();
  });
});

describe('resolveExpiredRestartPin', () => {
  it('resolves SUCCEEDED and clears the pin', async () => {
    useBotWorkshopStore.setState({ items: [withRestartResponse(codingBot(), codingRestartResponse, 1_000)] });
    mockedGetBot.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: {
        ext: { aicoding: { restart: { operation_id: 'op-1', status: 'SUCCEEDED' } } },
      },
      request_id: 'req-1',
    } as never);
    await expect(resolveExpiredRestartPin(useBotWorkshopStore.getState().items[0])).resolves.toEqual({
      status: 'SUCCEEDED',
    });
    expect(useBotWorkshopStore.getState().items[0].pendingRestart).toBeUndefined();
  });

  it('resolves FAILED with the published message', async () => {
    useBotWorkshopStore.setState({ items: [withRestartResponse(codingBot(), codingRestartResponse, 1_000)] });
    mockedGetBot.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: {
        ext: {
          aicoding: {
            restart: {
              operation_id: 'op-1',
              status: 'FAILED',
              phase: 'RESTARTING',
              message: '容器重启失败，请检查部署日志',
            },
          },
        },
      },
      request_id: 'req-1',
    } as never);
    const bot = useBotWorkshopStore.getState().items[0];
    await expect(resolveExpiredRestartPin(bot)).resolves.toEqual({
      status: 'FAILED',
      message: '容器重启失败，请检查部署日志',
    });
    expect(useBotWorkshopStore.getState().items[0].pendingRestart).toBeUndefined();
  });

  it('extends a slow RUNNING operation once, then falls back to the inventory', async () => {
    useBotWorkshopStore.setState({ items: [withRestartResponse(codingBot(), codingRestartResponse, 1_000)] });
    mockedGetBot.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { ext: { aicoding: { restart: { operation_id: 'op-1', status: 'RUNNING', phase: 'BACKING_UP' } } } },
      request_id: 'req-1',
    } as never);
    const first = useBotWorkshopStore.getState().items[0];
    await expect(resolveExpiredRestartPin(first)).resolves.toEqual({ status: 'RUNNING', extended: true });
    expect(useBotWorkshopStore.getState().items[0].pendingRestart).toMatchObject({ extensions: 1 });

    const second = useBotWorkshopStore.getState().items[0];
    await expect(resolveExpiredRestartPin(second)).resolves.toEqual({ status: 'RUNNING', extended: false });
    expect(useBotWorkshopStore.getState().items[0].pendingRestart).toBeUndefined();
  });

  it('falls back to inventory truth when the probe fails or the operation mismatches', async () => {
    useBotWorkshopStore.setState({ items: [withRestartResponse(codingBot(), codingRestartResponse, 1_000)] });
    mockedGetBot.mockRejectedValue(new Error('network'));
    await expect(resolveExpiredRestartPin(useBotWorkshopStore.getState().items[0])).resolves.toEqual({
      status: 'UNKNOWN',
    });
    expect(useBotWorkshopStore.getState().items[0].pendingRestart).toBeUndefined();

    useBotWorkshopStore.setState({ items: [withRestartResponse(codingBot(), codingRestartResponse, 1_000)] });
    mockedGetBot.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { ext: { aicoding: { restart: { operation_id: 'op-9', status: 'RUNNING' } } } },
      request_id: 'req-2',
    } as never);
    await expect(resolveExpiredRestartPin(useBotWorkshopStore.getState().items[0])).resolves.toEqual({
      status: 'UNKNOWN',
    });
    expect(useBotWorkshopStore.getState().items[0].pendingRestart).toBeUndefined();
  });
});

describe('readCodingRestartRecord', () => {
  it('ignores records without a usable operation id or status', () => {
    expect(readCodingRestartRecord({ ext: { aicoding: { restart: { operation_id: 'op', status: '' } } } })).toBeUndefined();
    expect(readCodingRestartRecord({ status: 'PENDING' })).toBeUndefined();
  });
});
