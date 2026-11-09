/** @jest-environment jsdom */

// 基础信息弹窗验证:名片实时预览、脏状态守卫、头像编辑器内仅暂存、点「保存修改」统一落库。
// 注意:jest.mock 工厂引用下方 const,`@jest/globals` 必须最先 import(工厂在组件模块求值时执行)。
import { expect, jest, test } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import type { BotDomain } from '@/domain/botWorkshop';
import { BotBasicInfoDialog } from '@/components/BotWorkshop/BotBasicInfoDialog';
import { mapBotDto } from '@/services/bots/botMapper';

const mockAvatarSave = jest.fn(async (id: string, value: string) => `${value}?saved=${id}&mock`);

jest.mock('@/services/botWorkshop/botAvatarService', () => ({
  botAvatarService: {
    get: jest.fn(async () => undefined),
    save: (id: string, value: string) => mockAvatarSave(id, value),
  },
}));

jest.mock('@/components/BotWorkshop/BotAvatar/AvatarSettings', () => ({
  AvatarSettings: ({ onStagedChange }: { onStagedChange?: (url: string | undefined) => void }) => (
    <div>
      头像设置
      <button type="button" onClick={() => onStagedChange?.('data:image/png;base64,new')}>
        暂存头像
      </button>
    </div>
  ),
}));

function renderDialog(overrides?: { bot?: BotDomain; editable?: boolean; open?: boolean }) {
  const bot = mapBotDto({ bot_id: 'bot-1', bot_name: '旧名称', bot_desc: '旧描述', engine: 'openclaw' }).item;
  const props = {
    bot,
    editable: true,
    open: true,
    saving: false,
    onOpenChange: jest.fn(),
    onSave: jest.fn(async () => undefined),
    onAvatarSaved: jest.fn(),
    ...overrides,
  };
  render(<BotBasicInfoDialog {...props} />);
  return props;
}

test('保存名称和描述，未暂存头像时不调头像接口', async () => {
  const { onOpenChange } = renderDialog();

  expect(screen.getByText('头像设置')).toBeInTheDocument();
  // 名片预览卡随输入实时更新名称
  expect(screen.getByText('旧名称')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '名称' }), { target: { value: '新名称' } });
  expect(screen.getByText('新名称')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '描述' }), { target: { value: '新的描述' } });
  fireEvent.click(screen.getByRole('button', { name: '保存修改' }));

  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  expect(mockAvatarSave).not.toHaveBeenCalled();
});

test('未做修改时保存按钮禁用，出现未保存提示', () => {
  renderDialog();

  expect(screen.getByRole('button', { name: '保存修改' })).toBeDisabled();
  expect(screen.queryByText('有未保存的修改')).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '名称' }), { target: { value: '新名称' } });
  expect(screen.getByRole('button', { name: '保存修改' })).toBeEnabled();
  expect(screen.getByText('有未保存的修改')).toBeInTheDocument();
});

test('编辑器内暂存头像不落库，点保存修改才调头像接口', async () => {
  const { onSave, onAvatarSaved, onOpenChange } = renderDialog();

  // 编辑器内选择头像 → 仅本地暂存，接口不调用
  fireEvent.click(screen.getByRole('button', { name: '暂存头像' }));
  expect(screen.getByRole('button', { name: '保存修改' })).toBeEnabled();
  expect(mockAvatarSave).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
  await waitFor(() => expect(mockAvatarSave).toHaveBeenCalledWith('bot-1', 'data:image/png;base64,new'));
  expect(onSave).toHaveBeenCalledWith({ name: '旧名称', description: '旧描述' });
  expect(onAvatarSaved).toHaveBeenCalled();
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

test('弹窗未打开或无编辑权限时不挂载头像设置', () => {
  const bot = mapBotDto({ bot_id: 'bot-1', bot_name: 'Bot', engine: 'openclaw' }).item;
  const props = {
    bot,
    saving: false,
    onOpenChange: jest.fn(),
    onSave: jest.fn(async () => undefined),
    onAvatarSaved: jest.fn(),
  };
  const { rerender } = render(<BotBasicInfoDialog {...props} editable open={false} />);
  expect(screen.queryByText('头像设置')).not.toBeInTheDocument();

  rerender(<BotBasicInfoDialog {...props} editable={false} open />);
  expect(screen.queryByText('头像设置')).not.toBeInTheDocument();
  // 只读态给出明确的方向性说明，而非只有一排禁用输入
  expect(screen.getByText('当前身份没有该 Bot 的编辑权限')).toBeInTheDocument();
});
