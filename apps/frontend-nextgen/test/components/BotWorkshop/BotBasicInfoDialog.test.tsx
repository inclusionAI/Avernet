/** @jest-environment jsdom */

import { BotBasicInfoDialog } from '@/components/BotWorkshop/BotBasicInfoDialog';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { expect, jest, test } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/components/BotWorkshop/BotAvatar/AvatarSettings', () => ({
  AvatarSettings: () => <div>头像设置</div>,
}));

test('保存名称和描述，并保留头像编辑入口', async () => {
  const bot = mapBotDto({ bot_id: 'bot-1', bot_name: '旧名称', bot_desc: '旧描述', engine: 'openclaw' }).item;
  const onSave = jest.fn(async () => undefined);
  const onOpenChange = jest.fn();

  render(
    <BotBasicInfoDialog
      bot={bot}
      editable
      open
      saving={false}
      onOpenChange={onOpenChange}
      onSave={onSave}
      onAvatarSaved={jest.fn()}
    />,
  );

  expect(screen.getByText('头像设置')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '名称' }), { target: { value: '新名称' } });
  fireEvent.change(screen.getByRole('textbox', { name: '描述' }), { target: { value: '新的描述' } });
  fireEvent.click(screen.getByRole('button', { name: '保存' }));

  await waitFor(() => expect(onSave).toHaveBeenCalledWith({ name: '新名称', description: '新的描述' }));
  expect(onOpenChange).toHaveBeenCalledWith(false);
});
