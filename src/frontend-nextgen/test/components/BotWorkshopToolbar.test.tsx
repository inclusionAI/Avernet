/** @jest-environment jsdom */
import BotWorkshopToolbar from '@/components/BotWorkshop/BotWorkshopToolbar';
import { fireEvent, render, screen } from '@testing-library/react';

it('服务类型下拉提供可清除条件的默认项', async () => {
  const onServiceModeChange = jest.fn();
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
  render(
    <BotWorkshopToolbar
      keyword=""
      engine=""
      serviceMode="service"
      onKeywordChange={jest.fn()}
      onEngineChange={jest.fn()}
      onDeploymentChange={jest.fn()}
      onServiceModeChange={onServiceModeChange}
      onCreateCloud={jest.fn()}
      onReset={jest.fn()}
    />,
  );

  // 已选中具体值时框内显示该值,不被 placeholder 覆盖
  expect(screen.getByRole('combobox', { name: '服务类型' }).textContent).toBe('服务化');

  fireEvent.click(screen.getByRole('combobox', { name: '服务类型' }));

  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(['全部', '服务化', '非服务化']);
  fireEvent.click(screen.getByRole('option', { name: '全部' }));
  expect(onServiceModeChange).toHaveBeenCalledWith(undefined);
});

it('引擎筛选下拉（Open Core 形态）提供 Claude Code 原生入口', async () => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
  render(
    <BotWorkshopToolbar
      keyword=""
      engine=""
      onKeywordChange={jest.fn()}
      onEngineChange={jest.fn()}
      onDeploymentChange={jest.fn()}
      onServiceModeChange={jest.fn()}
      onCreateCloud={jest.fn()}
      onReset={jest.fn()}
    />,
  );

  // engine="" → value='all',框内显示「全部」;筛选器名常驻左侧标题,不靠框内文案区分
  expect(screen.getByRole('combobox', { name: '引擎类型' }).textContent).toBe('全部');
  expect(screen.getByRole('combobox', { name: '部署方式' }).textContent).toBe('全部');

  fireEvent.click(screen.getByRole('combobox', { name: '引擎类型' }));

  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
    '全部',
    'OpenClaw',
    'Claudecode引擎-原生',
  ]);
  expect(screen.queryByRole('option', { name: 'AgentCoding' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'Hermes' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'TEClaw' })).toBeNull();
});

it('筛选器名常驻左侧标题,框内只反映当前选择', async () => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
  const onEngineChange = jest.fn();
  const onDeploymentChange = jest.fn();
  const onServiceModeChange = jest.fn();
  const base = {
    keyword: '',
    onKeywordChange: jest.fn(),
    onEngineChange,
    onDeploymentChange,
    onServiceModeChange,
    onCreateCloud: jest.fn(),
    onReset: jest.fn(),
  };
  const { rerender } = render(
    <BotWorkshopToolbar {...base} engine="openclaw" deployment="cloud" serviceMode="service" />,
  );

  // 选中具体值时框内显示该值
  expect(screen.getByRole('combobox', { name: '部署方式' }).textContent).toBe('云端');
  expect(screen.getByRole('combobox', { name: '服务类型' }).textContent).toBe('服务化');

  // 引擎的置空值是 ''(不同于另两个的 undefined),靠 `engine || 'all'` 回落到哨兵项
  fireEvent.click(screen.getByRole('combobox', { name: '引擎类型' }));
  fireEvent.click(screen.getByRole('option', { name: '全部' }));
  expect(onEngineChange).toHaveBeenCalledWith('');
  rerender(<BotWorkshopToolbar {...base} engine="" deployment="cloud" serviceMode="service" />);
  expect(screen.getByRole('combobox', { name: '引擎类型' }).textContent).toBe('全部');

  fireEvent.click(screen.getByRole('combobox', { name: '部署方式' }));
  fireEvent.click(screen.getByRole('option', { name: '全部' }));
  expect(onDeploymentChange).toHaveBeenCalledWith(undefined);
  rerender(<BotWorkshopToolbar {...base} engine="" deployment={undefined} serviceMode="service" />);
  expect(screen.getByRole('combobox', { name: '部署方式' }).textContent).toBe('全部');

  fireEvent.click(screen.getByRole('combobox', { name: '服务类型' }));
  fireEvent.click(screen.getByRole('option', { name: '全部' }));
  expect(onServiceModeChange).toHaveBeenCalledWith(undefined);
  rerender(<BotWorkshopToolbar {...base} engine="" deployment={undefined} serviceMode={undefined} />);
  expect(screen.getByRole('combobox', { name: '服务类型' }).textContent).toBe('全部');

  // 三个框内同为「全部」,只能靠左侧标题区分
  expect(screen.queryAllByRole('combobox').map((box) => box.textContent)).toEqual(['全部', '全部', '全部']);
  // 标题各只出现一次:在左侧常驻,不得再在框内重复
  for (const label of ['引擎类型', '部署方式', '服务类型']) {
    expect(screen.getAllByText(label)).toHaveLength(1);
  }
});
