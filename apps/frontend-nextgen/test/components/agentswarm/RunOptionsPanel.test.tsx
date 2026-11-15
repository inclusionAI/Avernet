/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { RunOptionsPanel, hasCustomLimits } from '@/components/agentswarm/RunOptionsPanel';
import { validateOptions } from '@/domain/agentswarm/options';

const renderPanel = (options: Record<string, unknown> | null, onChange = jest.fn()) =>
  render(<RunOptionsPanel options={options} onChange={onChange} />);

test('the limits are hidden until the switch is turned on, as in the PRD', () => {
  renderPanel({});
  expect(screen.queryByLabelText(/最大并发数/)).toBeNull();
  expect(screen.getByLabelText('自定义执行限制')).toBeInTheDocument();
});

test('turning the switch on seeds preset limits', () => {
  const onChange = jest.fn();
  renderPanel({}, onChange);
  fireEvent.click(screen.getByLabelText('自定义执行限制'));
  expect(onChange).toHaveBeenCalledTimes(1);
  const next = onChange.mock.calls[0][0];
  expect(next).toMatchObject({
    max_total_agents: 32,
    max_width: 3,
    max_turns: 64,
    max_attempts: 3,
    setup_timeout_seconds: 1800,
    per_attempt_timeout_seconds: 1800,
    slice_max_seconds: 3600,
  });
});

test('the seeded defaults validate — turning the switch on never opens in error', () => {
  const onChange = jest.fn();
  renderPanel({}, onChange);
  fireEvent.click(screen.getByLabelText('自定义执行限制'));
  const seeded = onChange.mock.calls[0][0];
  // The preset set must satisfy the same rules the form enforces.
  expect(validateOptions(seeded)).toEqual([]);
});

test('with limits on, every field is editable and shows its bounds', () => {
  renderPanel({ max_width: 3 });
  expect(screen.getByLabelText(/最大并发数/)).toHaveValue(3);
  expect(screen.getByText(/1–8/)).toBeInTheDocument();
});

test('editing one limit leaves the others intact', () => {
  const onChange = jest.fn();
  renderPanel({ max_width: 3, max_turns: 10 }, onChange);
  fireEvent.change(screen.getByLabelText(/最大并发数/), { target: { value: '5' } });
  expect(onChange).toHaveBeenCalledWith({ max_width: 5, max_turns: 10 });
});

test('turning the switch off clears limits but keeps the chosen model', () => {
  const onChange = jest.fn();
  renderPanel({ max_width: 3, model_name: 'GLM-5' }, onChange);
  fireEvent.click(screen.getByLabelText('自定义执行限制'));
  expect(onChange).toHaveBeenCalledWith({ model_name: 'GLM-5' });
});

test('the model selector sits outside the switch — it is not a limit', () => {
  renderPanel({ model_name: 'GLM-5' });
  expect(screen.getByText('运行默认模型')).toBeInTheDocument();
  expect(hasCustomLimits({ model_name: 'GLM-5' })).toBe(false);
});

test('an out-of-range limit is reported at its own field', () => {
  renderPanel({ max_width: 99 });
  expect(screen.getByLabelText(/最大并发数/)).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText(/1–8 之间/)).toBeInTheDocument();
});

test('a limit alone counts as custom; the model alone does not', () => {
  expect(hasCustomLimits({ max_width: 3 })).toBe(true);
  expect(hasCustomLimits({ model_name: 'GLM-5' })).toBe(false);
  expect(hasCustomLimits({})).toBe(false);
  expect(hasCustomLimits(null)).toBe(false);
});