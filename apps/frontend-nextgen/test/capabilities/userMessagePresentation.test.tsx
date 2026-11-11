/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { UserMessagePresentationHost } from '@/components/Workspace/UserMessagePresentationHost';
import { DebugUserMessage } from '@/components/BotWorkshop/Editor/DebugUserMessage';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

const original = 'reference template\n<file-ref name="guide.md" path="/fixture"></file-ref>question';
afterEach(() => extendCapabilities({ getUserMessagePresentation: defaultCapabilities.getUserMessagePresentation }));
it.each(['default', 'unsupported', 'unrecognized'])("%s presentation preserves the entire original", (mode) => {
  if (mode !== 'default') {
    extendCapabilities({ getUserMessagePresentation: () => ({
      status: mode === 'unsupported' ? 'unsupported' : 'available',
      value: () => mode === 'unrecognized' ? null : { content: 'wrong', header: <span>wrong</span> },
    }) });
  }
  const child = jest.fn((content) => <span>{content}</span>);
  render(<UserMessagePresentationHost content={original}>{child}</UserMessagePresentationHost>);
  expect(child).toHaveBeenCalledWith(original);
  expect(screen.queryByText('wrong')).not.toBeInTheDocument();
});
it('projects historical and current content identically without changing the input', () => {
  const presenter = jest.fn(() => ({ content: 'question', header: <span>{'<reference & name>'}</span> }));
  extendCapabilities({ getUserMessagePresentation: () => ({ status: 'available', value: presenter }) });
  const child = (content: string) => <p>{content}</p>;
  const view = render(<UserMessagePresentationHost content={original}>{child}</UserMessagePresentationHost>);
  expect(screen.getByText('<reference & name>')).toBeVisible();
  expect(screen.getByText('question')).toBeVisible();
  view.unmount();
  render(<UserMessagePresentationHost content={original}>{child}</UserMessagePresentationHost>);
  expect(presenter.mock.calls).toEqual([[original], [original]]);
});
it('debug projection keeps file cards and the user body', () => {
  extendCapabilities({ getUserMessagePresentation: () => ({ status: 'available', value: (content) => ({
    content: content.replace('reference template\n', ''), header: <span>reference card</span>,
  }) }) });
  render(<DebugUserMessage content={original} />);
  expect(screen.getByText('reference card')).toBeVisible();
  expect(screen.getByText('guide.md')).toBeVisible();
  expect(screen.getByText('question')).toBeVisible();
  expect(screen.queryByText(/reference template/)).not.toBeInTheDocument();
});
