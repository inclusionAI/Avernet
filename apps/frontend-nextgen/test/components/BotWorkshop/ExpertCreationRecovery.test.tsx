/** @jest-environment jsdom */
import { fireEvent, render, screen } from '@testing-library/react';
import { ExpertCreationResult } from '@/components/BotWorkshop/CreateBotModal/ExpertCreationResult';
import type { BotCreateInput } from '@/services/botWorkshop';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
const input: BotCreateInput = {
  scenario: 'cloud', name: '专家', description: '评审', engine: 'openclaw',
  spaceId: '10', ownership: 'personal', serviceMode: 'non-service', initialize: true,
};

test('失败结果展示当前 Apply 错误，并在原 Bot 提供显式恢复', () => {
  const recover = jest.fn();
  render(<ExpertCreationResult botId="same-bot" status={{
    bot_id: 'same-bot', iframe_url: '', redirect_url: '', state: 'APPLY_FAILED',
    apply: { apply_id: 'new-apply', result: 'PARTIAL', entries: [
      { category: 'skills', name: 'prd-review', action: 'failed', error: '凭证范围不允许' },
    ] },
  }} unknown={false} input={input} onClose={jest.fn()} onRecover={recover} />);
  expect(screen.getByText(/new-apply/)).toBeTruthy();
  expect(screen.getByText(/凭证范围不允许/)).toBeTruthy();
  expect(screen.getByText(/清理工作区/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '在原 Bot 重试配置' }));
  expect(recover).toHaveBeenCalledTimes(1);
});
