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

  test('提交普通讨论主题，保留默认类型', async () => {
    const user = userEvent.setup();
    const onOpenChange = jest.fn();
    const onPublish = jest.fn().mockResolvedValue(true);
    render(<PublishTopicModal open publishing={false} onOpenChange={onOpenChange} onPublish={onPublish} />);

    await user.type(screen.getByLabelText('主题标题'), '  社区如何沉淀知识  ');
    await user.type(screen.getByLabelText('正文'), '讨论内容');
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(onPublish).toHaveBeenCalledWith({
      title: '  社区如何沉淀知识  ',
      body: '讨论内容',
      topicType: 'discussion',
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  test('创建多选投票并转换选项输入', async () => {
    const user = userEvent.setup();
    const onPublish = jest.fn().mockResolvedValue(true);
    render(<PublishTopicModal open publishing={false} onOpenChange={jest.fn()} onPublish={onPublish} />);

    await user.type(screen.getByLabelText('主题标题'), '选择协作方案');
    await user.type(screen.getByLabelText('正文'), '请说明理由');
    await user.click(screen.getByRole('button', { name: '投票' }));
    await user.type(screen.getByLabelText('投票选项'), '方案 A\n方案 B');
    await user.click(screen.getByRole('button', { name: '多选' }));
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(onPublish).toHaveBeenCalledWith({
      title: '选择协作方案',
      body: '请说明理由',
      topicType: 'poll',
      poll: { selection: 'multi', options: ['方案 A', '方案 B'] },
    });
  });

  test('投票选项少于两个时阻止提交', async () => {
    const user = userEvent.setup();
    const onPublish = jest.fn();
    render(<PublishTopicModal open publishing={false} onOpenChange={jest.fn()} onPublish={onPublish} />);

    await user.type(screen.getByLabelText('主题标题'), '选择协作方案');
    await user.type(screen.getByLabelText('正文'), '请说明理由');
    await user.click(screen.getByRole('button', { name: '投票' }));
    await user.type(screen.getByLabelText('投票选项'), '只有一个选项');
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(screen.getByText('投票主题至少需要两个不同选项')).toBeInTheDocument();
    expect(onPublish).not.toHaveBeenCalled();
  });

  test('发布主题编辑视图展示返回箭头并可返回列表', async () => {
    const user = userEvent.setup();
    const onOpenChange = jest.fn();
    render(<PublishTopicModal open publishing={false} onOpenChange={onOpenChange} onPublish={jest.fn()} />);

    const publishButton = screen.getByRole('button', { name: '发布' });
    expect(publishButton).toHaveClass('bg-primary', 'text-primary-foreground');

    await user.click(screen.getByRole('button', { name: '返回社区列表' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
