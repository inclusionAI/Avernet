/** @jest-environment jsdom */
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}

      unobserve() {}

      disconnect() {}
    },
  });
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

test('创建云端 Bot 提供 Claude Code 原生和 AIcoding 引擎', async () => {
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  await userEvent.click(screen.getByRole('combobox', { name: '引擎类型' }));

  expect(screen.getByRole('option', { name: 'Claudecode引擎-原生' })).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Claudecode引擎-AIcoding' })).toBeTruthy();
});

test('选择 AIcoding 后关闭服务化并禁用服务开关', async () => {
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  await userEvent.click(screen.getByRole('combobox', { name: '引擎类型' }));
  await userEvent.click(screen.getByRole('option', { name: 'Claudecode引擎-AIcoding' }));

  expect(screen.getByRole('switch', { name: '是否提供服务' })).toHaveProperty('disabled', true);
  expect(screen.getByText('AIcoding 暂不支持')).toBeTruthy();
});

test('归属空间固定为当前空间且不提供空间选择器', () => {
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10002', name: '当前研发空间', ownership: 'team', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  expect(screen.getByText('当前研发空间')).toBeTruthy();
  expect(screen.getByText('跟随当前工作空间，不支持在创建时切换')).toBeTruthy();
  expect(screen.getAllByRole('combobox')).toHaveLength(1);
});
