/** @jest-environment jsdom */
import { EvalFileInput } from '@/components/BotWorkshop/ServiceBotEvaluation/EvalFileInput';
import { render, screen } from '@testing-library/react';

test('样本集上传提示展示支持的文件类型', () => {
  render(
    <EvalFileInput
      label="评测样本集"
      supportedFileTypes="JSONL、CSV、Excel"
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getByText(/JSONL、CSV、Excel/)).toBeTruthy();
});

test('报告上传提示展示支持的文件类型', () => {
  render(
    <EvalFileInput
      label="评测结果报告"
      supportedFileTypes="HTML、Markdown、JSON、TXT、PDF、Excel、Word"
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getByText(/HTML、Markdown、JSON、TXT、PDF、Excel、Word/)).toBeTruthy();
});

test('未传入 supportedFileTypes 时不展示支持类型文案', () => {
  render(<EvalFileInput label="评测目标文件" onSelect={jest.fn()} />);

  expect(screen.queryByText(/支持文件类型/)).toBeNull();
});
