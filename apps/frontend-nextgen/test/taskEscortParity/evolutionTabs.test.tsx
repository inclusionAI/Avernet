/** @jest-environment jsdom */
import { ReadTabs } from '@/pages/TaskEscort/parity/evolution/ReadPrimitives';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
it('roves three tabs with arrows/wrapping/Home/End, updating both focus and selection', () => {
  function TabsFixture() {
    const [tab, setTab] = useState('causes');
    return (
      <ReadTabs
        label="问题详情分区"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'causes', label: '问题原因' },
          { value: 'repairs', label: '修复建议' },
          { value: 'evidence', label: '证据与历史' },
        ]}
      />
    );
  }
  render(<TabsFixture />);
  const [first, second, third] = screen.getAllByRole('tab');
  expect(first).toHaveAttribute('tabindex', '0');
  expect(second).toHaveAttribute('tabindex', '-1');
  expect(third).toHaveAttribute('tabindex', '-1');
  first.focus();
  fireEvent.keyDown(first, { key: 'ArrowRight' });
  expect(second).toHaveFocus();
  expect(second).toHaveAttribute('aria-selected', 'true');
  expect(first).toHaveAttribute('tabindex', '-1');
  fireEvent.keyDown(second, { key: 'End' });
  expect(third).toHaveFocus();
  expect(third).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(third, { key: 'ArrowRight' });
  expect(first).toHaveFocus();
  fireEvent.keyDown(first, { key: 'ArrowLeft' });
  expect(third).toHaveFocus();
  fireEvent.keyDown(third, { key: 'Home' });
  expect(first).toHaveFocus();
  expect(first).toHaveAttribute('aria-selected', 'true');
  expect(first).toHaveAttribute('tabindex', '0');
});
