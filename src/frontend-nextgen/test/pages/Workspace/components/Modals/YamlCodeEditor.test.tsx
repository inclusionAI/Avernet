/** @jest-environment jsdom */
import { YamlCodeEditor } from '@/pages/Workspace/components/Modals/YamlEditor/YamlCodeEditor';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

it('fills the available YAML section height instead of staying fixed at 320px', () => {
  render(<YamlCodeEditor value="" onChange={jest.fn()} loading fillAvailableHeight />);

  const editor = screen.getByTestId('yaml-code-editor');
  expect(editor).toHaveClass('min-h-[320px]', 'flex-1');
  expect(editor.style.height).toBe('');
});
