/** @jest-environment jsdom */

import { AvatarSettings } from '@/components/BotWorkshop/BotAvatar/AvatarSettings';
import { appExtension } from '@/extensions';
import { expect, jest, test } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentType } from 'react';

jest.mock('@/hooks/useBotAvatar', () => ({
  useBotAvatar: () => ({
    value: '',
    setValue: jest.fn(),
    loading: false,
    saving: false,
    error: '',
    save: jest.fn(async () => 'avatar.png'),
  }),
}));

// 模拟装配形态：默认 collab（avatarEditor 槽位 null），第二用例内改写为注册真实编辑器的 Open 形态。
jest.mock('@/extensions', () => ({
  appExtension: { avatarEditor: null, capabilities: {} },
}));

test('collab 形态 avatarEditor 槽位为 null：整块不渲染，无头像按钮与保存按钮', () => {
  const { container } = render(<AvatarSettings botId="bot-1" editable />);

  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByRole('button', { name: '头像' })).not.toBeInTheDocument();
});

test('注册编辑器槽位后：打开头像弹层渲染编辑器与暂存提示（保存由宿主弹窗统一落库）', async () => {
  const extensions = appExtension as unknown as { avatarEditor: ComponentType | null };
  extensions.avatarEditor = () => <div>头像编辑器桩</div>;
  render(<AvatarSettings botId="bot-1" editable />);

  fireEvent.click(screen.getByRole('button', { name: '头像' }));
  expect(await screen.findByText('头像编辑器桩')).toBeInTheDocument();
  expect(screen.getByText('修改仅本地暂存，点击弹窗的「保存修改」后生效')).toBeInTheDocument();
});
