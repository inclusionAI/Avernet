/** @jest-environment jsdom */
/**
 * The 添加 Agent picker.
 *
 * Its tabs are four sources, but only one is wired in this milestone. The other
 * two were listed without being disabled, so they took the click and left the
 * library on screen — the tab said 空间 Bot while the body showed definitions.
 */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { DefinitionPicker } from '@/components/agentswarm/DefinitionPicker';

jest.mock('@/services/agentswarm/service', () => ({
  listDefinitions: jest.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 }),
  createDefinition: jest.fn(),
  updateDefinition: jest.fn(),
  deleteDefinition: jest.fn(),
}));

/** A source tab: Segmented renders toggle buttons, not radios. */
function tab(name: string): HTMLElement {
  return screen.getByRole('button', { name });
}

function renderPicker() {
  return render(
    <DefinitionPicker
      tenant="t"
      open
      onOpenChange={() => {}}
      onAdd={() => {}}
    />,
  );
}

describe('DefinitionPicker sources', () => {
  test('the unimplemented catalogues are disabled', () => {
    renderPicker();

    // Disabled, not merely annotated: clicking must do nothing.
    expect(tab('空间 Bot')).toBeDisabled();
    expect(tab('专家市场')).toBeDisabled();
  });

  test('the library is the source that is actually on', () => {
    renderPicker();

    expect(tab('我的 Agent 库')).toBeEnabled();
    expect(screen.getByLabelText('搜索定义')).toBeInTheDocument();
  });

  test('an unselected catalogue does not render the library underneath', () => {
    // Guard against the tab and the body disagreeing: whatever the source says,
    // the body must only show a list it actually loaded.
    renderPicker();
    const library = tab('我的 Agent 库');
    fireEvent.click(tab('空间 Bot'));

    // A disabled tab takes no click, so the library stays the selected source
    // and its list stays on screen.
    expect(library).toHaveAttribute('aria-pressed', 'true');
    // And no "not available" notice either: the source never changed.
    expect(screen.queryByText(/本期只接入/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('搜索定义')).toBeInTheDocument();
  });
});
