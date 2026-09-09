import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import CreateSessionControl from '../CreateSessionControl';

const mockButtons: any[] = [];
jest.mock('@/components', () => ({
  Button: (props: any) => {
    mockButtons.push(props);
    return null;
  },
}));
jest.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: any) => children,
  PopoverContent: ({ children }: any) => children,
  PopoverTrigger: ({ children }: any) => children,
}));

describe('new session scope selection', () => {
  beforeEach(() => { mockButtons.length = 0; });

  it.each([true, false])('inherits group scope on the main button (menu=%s)', (showScopeMenu) => {
    const onCreateSession = jest.fn();
    renderToStaticMarkup(React.createElement(CreateSessionControl, {
      isCreating: false, showScopeMenu, onCreateSession,
    }));
    mockButtons[0].onClick();
    expect(onCreateSession).toHaveBeenCalledWith();
  });

  it.each(['full', 'participant'])('allows an explicit %s override from the menu', (scope) => {
    const onCreateSession = jest.fn();
    renderToStaticMarkup(React.createElement(CreateSessionControl, {
      isCreating: false, showScopeMenu: true, onCreateSession,
    }));
    const options = mockButtons.filter((button) => button.role === 'menuitem');
    options[scope === 'full' ? 0 : 1].onClick();
    expect(onCreateSession).toHaveBeenCalledWith(scope);
  });
});
