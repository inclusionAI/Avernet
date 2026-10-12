/** @jest-environment jsdom */
import { PublishTopicModal } from '@/components/Community/PublishTopicModal';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;

describe('PublishTopicModal', () => {
  test('空标题和正文阻止提交并展示行内错误', async () => {
    const user = userEvent.setup();
    const onPublish = jest.fn();
    render(<PublishTopicModal open publishing={false} onOpenChange={jest.fn()} onPublish={onPublish} />);

    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(screen.getByText('请输入主题标题')).toBeInTheDocument();
    expect(screen.getByText('请输入主题正文')).toBeInTheDocument();
    expect(onPublish).not.toHaveBeenCalled();
  });

  test('提交单一主题模型，成功后关闭弹窗，且不存在类型或投票入口', async () => {
    const user = userEvent.setup();
    const onOpenChange = jest.fn();
    const onPublish = jest.fn().mockResolvedValue(true);
    render(<PublishTopicModal open publishing={false} onOpenChange={onOpenChange} onPublish={onPublish} />);

    await user.type(screen.getByLabelText('主题标题'), '  社区如何沉淀知识  ');
    await user.type(screen.getByLabelText('正文'), '讨论内容');
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(onPublish).toHaveBeenCalledWith('  社区如何沉淀知识  ', '讨论内容');
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(screen.queryByText(/主题类型|投票/)).not.toBeInTheDocument();
  });
});
