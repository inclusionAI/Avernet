/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { fireEvent, render, screen } from '@testing-library/react';

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

afterEach(() => {
  // 注入过 overlay 引擎清单的用例退出时还原 Open 默认，避免污染同文件后续用例。
  extendCapabilities({ getBotEngineOptions: defaultCapabilities.getBotEngineOptions });
});

test('创建云端 Bot（Open Core 形态）仅提供 OpenClaw，不展示 Claude Code 原生入口', async () => {
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('combobox', { name: '引擎类型' }));

  expect(screen.getByRole('option', { name: 'OpenClaw' })).toBeTruthy();
  expect(screen.queryByRole('option', { name: 'Claudecode引擎-原生' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'AgentCoding' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'Hermes' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'TEClaw' })).toBeNull();
});

test('internal overlay 引擎清单下，选择 AIcoding 后关闭服务化并禁用服务开关', async () => {
  // 模拟内部形态：经 capability 注入含 aicoding 的清单（全量清单语义见
  // test/internal/brandAndEngineCapabilities.test.ts），覆盖非服务化联动分支逻辑。
  extendCapabilities({
    getBotEngineOptions: () => ({
      status: 'available',
      value: [
        { value: 'openclaw', label: 'OpenClaw' },
        { value: 'aicoding', label: 'AgentCoding' },
      ],
    }),
  });
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('combobox', { name: '引擎类型' }));
  fireEvent.click(screen.getByRole('option', { name: 'AgentCoding' }));

  expect(screen.getByRole('switch', { name: '是否提供服务' })).toHaveProperty('disabled', true);
  expect(screen.getByText('AgentCoding 暂不支持')).toBeTruthy();
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
