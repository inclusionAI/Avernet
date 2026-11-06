/** @jest-environment jsdom */
/**
 * BotModelSelector 模型徽标用例：下拉项徽标渲染、徽标点击新窗口跳转不触发选模型、
 * 选中态触发按钮不渲染徽标。
 * 契约见 local-docs/模型选择器推荐徽标_契约与验收.md（OCB 内源仓）。
 */
import { BotModelSelector } from '@/components/Workspace/ChatPanel/BotModelSelector';
import type { BotModelView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// 原文件口径：jsdom 下 Radix 异步渲染开销大，保留 60s 上限（仅本文件生效）。
jest.setTimeout(60000);

const models: BotModelView[] = [
  { modelId: 'model-1', name: 'Model 1', provider: 'antchat' },
  {
    modelId: 'cli_venus',
    name: 'Cli_venus',
    provider: 'antchat',
    badge: { label: '推荐', link: 'https://example.com/venus' },
  },
];

const openMock = jest.spyOn(window, 'open').mockImplementation(() => null);

beforeEach(() => {
  openMock.mockClear();
});

it('下拉项徽标随 badge 渲染，徽标点击新窗口跳转且不触发选模型、不收起下拉', async () => {
  const onSelect = jest.fn();
  render(<BotModelSelector models={models} activeModelId="model-1" onSelect={onSelect} />);

  fireEvent.click(screen.getByRole('button', { name: 'Model 1' }));
  const badge = await screen.findByText('推荐');
  fireEvent.click(badge);

  expect(openMock).toHaveBeenCalledWith('https://example.com/venus', '_blank', 'noopener,noreferrer');
  expect(onSelect).not.toHaveBeenCalled();
  // 徽标点击不收起下拉：列表项仍在文档中。
  expect(screen.getByText('Cli_venus')).toBeInTheDocument();
});

it('选中徽标模型时，徽标仅出现在下拉列表项中，触发按钮不渲染徽标', async () => {
  render(<BotModelSelector models={models} activeModelId="cli_venus" onSelect={jest.fn()} />);

  fireEvent.click(screen.getByRole('button', { name: /Cli_venus/ }));
  // 打开下拉后「推荐」仅出现一次（列表项），触发按钮上无徽标。
  await screen.findByText('推荐');
  expect(screen.getAllByText('推荐')).toHaveLength(1);
});

it('无 badge 的模型项不渲染徽标', async () => {
  render(<BotModelSelector models={[models[0]]} activeModelId="model-1" onSelect={jest.fn()} />);

  fireEvent.click(screen.getByRole('button', { name: 'Model 1' }));
  // 用 provider 徽标文本等列表项挂载（触发按钮 label 与模型名同名，'Model 1' 会双匹配）。
  await screen.findByText('antchat');
  expect(screen.queryByText('推荐')).not.toBeInTheDocument();
});
