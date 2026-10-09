import { resolveHistoryPrependTargetId } from '@/pages/Workspace/hooks/useHistoryPrependAnchor';
import { expect, it } from '@jest/globals';

it('前置一批历史后定位到原最旧消息的上一条，而不是新批次最旧消息', () => {
  expect(
    resolveHistoryPrependTargetId(
      ['current-oldest', 'newer'],
      ['batch-oldest', 'batch-middle', 'previous', 'current-oldest', 'newer'],
    ),
  ).toBe('previous');
});

it('没有成功前置消息时不生成滚动目标', () => {
  expect(resolveHistoryPrependTargetId(['current-oldest'], ['current-oldest', 'newer'])).toBeNull();
});
