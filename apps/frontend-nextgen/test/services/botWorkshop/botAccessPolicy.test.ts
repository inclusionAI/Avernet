import type { BotDomain } from '@/services/botWorkshop';
import { canEnterBotDetail } from '@/services/botWorkshop/botPolicy';

const bot = (ownerId: string, actions: string[]) => ({ ownerId, actions } as BotDomain);

test('Owner 和明确具有 edit action 的协作者可进入详情', () => {
  expect(canEnterBotDetail(bot('1001', ['view']), '1001')).toBe(true);
  expect(canEnterBotDetail(bot('1001', ['view', 'edit']), '2002')).toBe(true);
});

test('只有公开 view action 的非协作者只能查看基础信息', () => {
  expect(canEnterBotDetail(bot('1001', ['view']), '2002')).toBe(false);
  expect(canEnterBotDetail(bot('1001', ['view']), undefined)).toBe(false);
});
