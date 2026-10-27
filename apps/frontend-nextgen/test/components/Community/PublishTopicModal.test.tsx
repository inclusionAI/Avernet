/** @jest-environment jsdom */
import { PublishTopicModal } from '@/components/Community/PublishTopicModal';
import type { CommunityAttachment } from '@/domain/community/types';
import { MAX_COMMUNITY_ATTACHMENT_BYTES } from '@/services/community/communityService';
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;

const uploadedAttachment: CommunityAttachment = {
  id: 'attachment-1',
  filename: '方案.pdf',
  contentType: 'application/pdf',
  sizeBytes: 1024,
};

function renderEditor(
  overrides: Partial<React.ComponentProps<typeof PublishTopicModal>> = {},
) {
  const props: React.ComponentProps<typeof PublishTopicModal> = {
    open: true,
    publishing: false,
    onOpenChange: jest.fn(),
    onPublish: jest.fn().mockResolvedValue(true),
    onUploadAttachment: jest.fn().mockResolvedValue(uploadedAttachment),
    ...overrides,
  };
  return { props, ...render(<PublishTopicModal {...props} />) };
}

describe('PublishTopicModal', () => {
  test('展示讨论、分享、投票、求助四种发布类型', () => {
    renderEditor();
    const group = screen.getByRole('group', { name: '主题类型' });
    expect(group).toHaveTextContent('讨论');
    expect(group).toHaveTextContent('分享');
    expect(group).toHaveTextContent('投票');
    expect(group).toHaveTextContent('求助');
  });

  test('空标题和正文阻止提交并展示行内错误', async () => {
    const user = userEvent.setup();
    const onPublish = jest.fn();
    renderEditor({ onPublish });

    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(screen.getByText('请输入主题标题')).toBeInTheDocument();
    expect(screen.getByText('请输入主题正文')).toBeInTheDocument();
    expect(onPublish).not.toHaveBeenCalled();
  });

  test('提交普通讨论主题，保留默认类型', async () => {
    const user = userEvent.setup();
    const { props } = renderEditor();

    await user.type(screen.getByLabelText('主题标题'), '  社区如何沉淀知识  ');
    await user.type(screen.getByLabelText('正文'), '讨论内容');
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(props.onPublish).toHaveBeenCalledWith({
      title: '  社区如何沉淀知识  ',
      body: '讨论内容',
      topicType: 'discussion',
    });
    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  test('分享主题没有附件时仍可发布', async () => {
    const user = userEvent.setup();
    const { props } = renderEditor();

    await user.click(screen.getByRole('button', { name: '分享' }));
    await user.type(screen.getByLabelText('主题标题'), '分享经验');
    await user.type(screen.getByLabelText('正文'), '正文内容');

    const publishButton = screen.getByRole('button', { name: '发布' });
    expect(publishButton).toBeEnabled();
    await user.click(publishButton);

    expect(props.onPublish).toHaveBeenCalledWith({
      title: '分享经验',
      body: '正文内容',
      topicType: 'share',
      share: { attachmentIds: [] },
    });
  });

  test('分享主题立即上传真实文件，成功后使用 attachment id 发布', async () => {
    const user = userEvent.setup();
    const { props } = renderEditor();
    const file = new File(['real bytes'], '方案.pdf', { type: 'application/pdf' });

    await user.click(screen.getByRole('button', { name: '分享' }));
    expect(screen.getByRole('button', { name: '发布' })).toBeEnabled();
    await user.upload(screen.getByLabelText('选择分享附件'), file);

    await waitFor(() => expect(props.onUploadAttachment).toHaveBeenCalledWith(file));
    await waitFor(() => expect(screen.getByText(/上传成功/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '发布' })).toBeEnabled();

    await user.type(screen.getByLabelText('主题标题'), '分享方案');
    await user.type(screen.getByLabelText('正文'), '供大家下载');
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(props.onPublish).toHaveBeenCalledWith({
      title: '分享方案',
      body: '供大家下载',
      topicType: 'share',
      share: { attachmentIds: ['attachment-1'] },
    });
  });

  test('空文件、超限文件和上传失败文件不能发布，失败文件可以重试', async () => {
    const user = userEvent.setup();
    const onUploadAttachment = jest
      .fn()
      .mockRejectedValueOnce(new Error('对象存储不可用'))
      .mockResolvedValueOnce(uploadedAttachment);
    renderEditor({ onUploadAttachment });
    await user.click(screen.getByRole('button', { name: '分享' }));

    const tooLarge = new File(['x'], 'large.zip');
    Object.defineProperty(tooLarge, 'size', { value: MAX_COMMUNITY_ATTACHMENT_BYTES + 1 });
    await user.upload(screen.getByLabelText('选择分享附件'), tooLarge);
    expect(screen.getByText(/单个附件不能超过 20 MiB/)).toBeInTheDocument();
    expect(onUploadAttachment).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '移除附件 large.zip' }));

    const valid = new File(['content'], 'retry.txt', { type: 'text/plain' });
    await user.upload(screen.getByLabelText('选择分享附件'), valid);
    await waitFor(() => expect(screen.getByText(/对象存储不可用/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: '重试上传 retry.txt' }));
    await waitFor(() => expect(screen.getByText(/上传成功/)).toBeInTheDocument());
    expect(onUploadAttachment).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: '发布' })).toBeEnabled();
  });

  test('创建多选投票，第三个选项开始可删除，最多十项', async () => {
    const user = userEvent.setup();
    const { props } = renderEditor();

    await user.type(screen.getByLabelText('主题标题'), '选择协作方案');
    await user.type(screen.getByLabelText('正文'), '请说明理由');
    await user.click(screen.getByRole('button', { name: '投票' }));
    await user.type(screen.getByLabelText('选项 1'), '方案 A');
    await user.type(screen.getByLabelText('选项 2'), '方案 B');
    await user.click(screen.getByRole('button', { name: '+ 添加选项' }));
    expect(screen.getByRole('button', { name: '删除选项 3' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '删除选项 3' }));

    for (let index = 0; index < 8; index += 1) {
      await user.click(screen.getByRole('button', { name: '+ 添加选项' }));
    }
    expect(screen.getByRole('button', { name: '+ 添加选项' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: '多选' }));
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(props.onPublish).toHaveBeenCalledWith({
      title: '选择协作方案',
      body: '请说明理由',
      topicType: 'poll',
      poll: { selection: 'multi', options: ['方案 A', '方案 B'] },
    });
  });

  test('投票选项少于两个时阻止提交', async () => {
    const user = userEvent.setup();
    const onPublish = jest.fn();
    renderEditor({ onPublish });

    await user.type(screen.getByLabelText('主题标题'), '选择协作方案');
    await user.type(screen.getByLabelText('正文'), '请说明理由');
    await user.click(screen.getByRole('button', { name: '投票' }));
    await user.type(screen.getByLabelText('选项 1'), '只有一个选项');
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(screen.getByText('投票主题至少需要两个有效选项')).toBeInTheDocument();
    expect(onPublish).not.toHaveBeenCalled();
  });

  test('求助主题提交可选期望解决时间', async () => {
    const user = userEvent.setup();
    const { props } = renderEditor();

    await user.click(screen.getByRole('button', { name: '求助' }));
    expect(screen.getByRole('region', { name: '求助设置' })).toBeInTheDocument();
    await user.type(screen.getByLabelText('主题标题'), '接口联调失败');
    await user.type(screen.getByLabelText('正文'), '需要一起排查');
    await user.type(screen.getByPlaceholderText('如：希望本周内'), ' 希望本周内 ');
    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(props.onPublish).toHaveBeenCalledWith({
      title: '接口联调失败',
      body: '需要一起排查',
      topicType: 'help',
      help: { expectedTime: '希望本周内' },
    });
  });

  test('发布主题编辑视图展示返回箭头并可返回列表', async () => {
    const user = userEvent.setup();
    const { props } = renderEditor();

    const publishButton = screen.getByRole('button', { name: '发布' });
    expect(publishButton).toHaveClass('bg-primary', 'text-primary-foreground');

    await user.click(screen.getByRole('button', { name: '返回社区列表' }));
    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });
});
