import { FeedbackService } from '@/services/feedback/feedbackService';

jest.mock('@/services/backendApi/feedback/feedbackController', () => ({
  createFeedback: jest.fn(),
}));
const { createFeedback } = jest.requireMock('@/services/backendApi/feedback/feedbackController') as {
  createFeedback: jest.Mock;
};

describe('FeedbackService', () => {
  beforeEach(() => jest.clearAllMocks());

  test('校验通过并解码成功信封返回 id', async () => {
    createFeedback.mockResolvedValue({ code: 201000, message: 'created', data: { id: 42 } });
    const id = await new FeedbackService().createFeedback({ reporterId: '900003', module: 'bbs', content: '好建议' });
    expect(createFeedback).toHaveBeenCalledWith({ reporterId: '900003', module: 'bbs', content: '好建议' }, undefined);
    expect(id).toEqual({ id: 42 });
  });

  test('空 content 被前端拦截，不发起请求', async () => {
    await expect(
      new FeedbackService().createFeedback({ reporterId: '900003', module: 'bbs', content: '   ' }),
    ).rejects.toThrow('请输入反馈内容');
    expect(createFeedback).not.toHaveBeenCalled();
  });

  test('超长 content 被前端拦截', async () => {
    await expect(
      new FeedbackService().createFeedback({ reporterId: '900003', module: 'bbs', content: 'x'.repeat(4001) }),
    ).rejects.toThrow('4000');
    expect(createFeedback).not.toHaveBeenCalled();
  });

  test('失败信封抛错', async () => {
    createFeedback.mockResolvedValue({ code: 500000, message: 'boom', data: null });
    await expect(
      new FeedbackService().createFeedback({ reporterId: '900003', module: 'bbs', content: 'x' }),
    ).rejects.toThrow('boom');
  });
});
