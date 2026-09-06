/** @jest-environment jsdom */
import { RoutineTaskTab } from '@/pages/MyTask/components/RoutineTaskTab';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

const baseProps: React.ComponentProps<typeof RoutineTaskTab> = {
  routines: [],
  total: 0,
  page: 1,
  pageSize: 10,
  loading: false,
  error: null,
  botOptions: [{ value: 'bot-1', label: '当前 Bot' }],
  selectedBotId: 'bot-1',
  onChangeBotId: jest.fn(),
  onRetry: jest.fn(),
  onSelectRoutine: jest.fn(),
  onRunRoutine: jest.fn().mockResolvedValue(undefined),
  onPageChange: jest.fn(),
  onPageSizeChange: jest.fn(),
  botNameMap: { 'bot-1': '当前 Bot' },
};

describe('RoutineTaskTab Bot selector visibility', () => {
  it('当前 Bot 身份模式隐藏 Bot 下拉', () => {
    render(<RoutineTaskTab {...baseProps} showBotSelector={false} />);

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('搜索任务名称 / 提示词 / 频率')).toBeInTheDocument();
  });

  it('默认模式保留 Bot 下拉', () => {
    render(<RoutineTaskTab {...baseProps} />);

    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });
});
