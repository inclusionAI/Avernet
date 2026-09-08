import { BackendRequestError } from '@/services/backendApi/httpClient';
import { getBotManagementErrorMessage } from '@/services/botWorkshop/botWorkshopErrorPolicy';

test('Bot 删除不支持的 409000 映射为中文业务提示', () => {
  const error = new BackendRequestError('Operation not supported for this bot', {
    status: 409,
    apiPath: '/openapi/v1/bots/bot-1',
    data: { code: 409000, message: 'Operation not supported for this bot', data: null },
  });

  expect(getBotManagementErrorMessage('delete', error, 'Bot 删除失败')).toBe('该 Bot 不允许删除');
});

test('其他删除错误继续优先展示后端明确消息', () => {
  const error = new BackendRequestError('Bot is publishing', {
    status: 409,
    apiPath: '/openapi/v1/bots/bot-1',
    data: { code: 409000, message: 'Bot is publishing' },
  });

  expect(getBotManagementErrorMessage('delete', error, 'Bot 删除失败')).toBe('Bot is publishing');
});

test('同一错误不影响其他 Bot 操作', () => {
  const error = new BackendRequestError('Operation not supported for this bot', {
    status: 409,
    apiPath: '/openapi/v1/bots/bot-1/restart',
    data: { code: 409000, message: 'Operation not supported for this bot' },
  });

  expect(getBotManagementErrorMessage('restart', error)).toBe('Operation not supported for this bot');
});
