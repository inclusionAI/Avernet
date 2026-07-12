import { useExt } from '@/capabilities';
import React from 'react';
import { useRegisterToken } from '../hooks/useRegisterToken';
import { HERMES_MULTI_PROFILE_NOTICE } from '../lib/botAccess';
import AccessSection from './AccessSection';
import { HermesBotConfigFields } from './HermesBotConfigFields';

jest.mock('@/capabilities', () => ({
  ...jest.requireActual('@/capabilities'),
  useExt: jest.fn(),
}));
jest.mock('../hooks/useRegisterToken', () => ({
  useRegisterToken: jest.fn(),
}));

const resources = {
  bcnConnectCmdTemplate: 'openclaw manual {token}',
  bcnAutoConnectCmdTemplate: 'openclaw automatic {token}',
  bcnHermesConnectCmdTemplate:
    'hermes manual {token} --bot-name {bot_name} --profile {profile} --create-profile',
  bcnHermesAutoConnectCmdTemplate: 'hermes automatic {token}',
};

type TestElement = React.ReactElement<any, any>;

// The repository's Jest setup is node-only, so drive real component hooks and
// event handlers without introducing a DOM test dependency.
function createRenderer<P>(Component: React.FC<P>, props: P) {
  const states: unknown[] = [];
  let hookIndex = 0;
  const dispatcherRef = (React as any)
    .__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
  const dispatcher = {
    useState(initialValue: unknown) {
      const index = hookIndex++;
      if (!(index in states)) {
        states[index] =
          typeof initialValue === 'function'
            ? (initialValue as () => unknown)()
            : initialValue;
      }
      return [
        states[index],
        (nextValue: unknown) => {
          states[index] =
            typeof nextValue === 'function'
              ? (nextValue as (value: unknown) => unknown)(states[index])
              : nextValue;
        },
      ];
    },
    useMemo(factory: () => unknown) {
      hookIndex++;
      return factory();
    },
  };

  return () => {
    hookIndex = 0;
    const previousDispatcher = dispatcherRef.current;
    dispatcherRef.current = dispatcher;
    try {
      return Component(props) as TestElement;
    } finally {
      dispatcherRef.current = previousDispatcher;
    }
  };
}

function resolvedChildren(node: TestElement): React.ReactNode[] {
  if (node.type === HermesBotConfigFields) {
    return [HermesBotConfigFields(node.props)];
  }
  return React.Children.toArray(node.props.children);
}

function elementsIn(node: React.ReactNode): TestElement[] {
  if (!React.isValidElement(node)) return [];
  const element = node as TestElement;
  return [
    element,
    ...resolvedChildren(element).flatMap((child) => elementsIn(child)),
  ];
}

function textOf(node: React.ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node);
  }
  if (!React.isValidElement(node)) return '';
  return resolvedChildren(node as TestElement)
    .map(textOf)
    .join('');
}

function getButton(tree: TestElement, label: string): TestElement {
  const button = elementsIn(tree).find(
    (element) => element.type === 'button' && textOf(element).trim() === label,
  );
  if (!button) throw new Error(`Button not found: ${label}`);
  return button;
}

function getInput(tree: TestElement, id: string): TestElement {
  const input = elementsIn(tree).find(
    (element) => element.type === 'input' && element.props.id === id,
  );
  if (!input) throw new Error(`Input not found: ${id}`);
  return input;
}

function getCopyButtons(tree: TestElement): TestElement[] {
  return elementsIn(tree).filter(
    (element) =>
      element.type === 'button' && element.props.title === '复制指令',
  );
}

function commandsIn(tree: TestElement): string[] {
  return elementsIn(tree)
    .filter((element) => element.type === 'code')
    .map(textOf);
}

function countText(tree: TestElement, text: string): number {
  return elementsIn(tree).filter(
    (element) => element.type === 'p' && textOf(element) === text,
  ).length;
}

describe('AccessSection Hermes configuration', () => {
  beforeEach(() => {
    (useExt as jest.Mock).mockReturnValue({ resources });
    (useRegisterToken as jest.Mock).mockReturnValue({
      token: 'registration-token',
      expiresAt: null,
      isLoading: false,
      fetchToken: jest.fn(),
    });
  });

  it('keeps selection error-free while invalid manual config blocks only Hermes manual copy', () => {
    const render = createRenderer(AccessSection, {});
    let tree = render();

    expect(commandsIn(tree)).toEqual([
      'openclaw manual registration-token',
      'openclaw automatic registration-token',
    ]);
    expect(getCopyButtons(tree).map((button) => button.props.disabled)).toEqual(
      [false, false],
    );

    getButton(tree, 'Hermes').props.onClick();
    tree = render();

    expect(textOf(tree)).not.toContain('请输入 Bot 名称');
    expect(textOf(tree)).not.toContain('请输入 Profile 名称');
    expect(
      getInput(tree, 'bcn-access-hermes-manual-bot-name').props[
        'aria-describedby'
      ],
    ).toBeUndefined();
    expect(
      getInput(tree, 'bcn-access-hermes-manual-profile').props[
        'aria-describedby'
      ],
    ).toBeUndefined();
    expect(getCopyButtons(tree).map((button) => button.props.disabled)).toEqual(
      [true, false],
    );
    expect(commandsIn(tree)).toEqual(['hermes automatic registration-token']);
    expect(textOf(tree)).toContain('请先填写有效的 Bot 名称和 Profile。');
    expect(countText(tree, HERMES_MULTI_PROFILE_NOTICE)).toBe(1);

    getInput(tree, 'bcn-access-hermes-manual-bot-name').props.onChange({
      target: { value: 'Hermes Reviewer' },
    });
    tree = render();
    getInput(tree, 'bcn-access-hermes-manual-profile').props.onChange({
      target: { value: 'INVALID_PROFILE' },
    });
    tree = render();

    expect(commandsIn(tree)).toEqual(['hermes automatic registration-token']);
    expect(textOf(tree)).not.toContain('--profile');
    expect(getCopyButtons(tree)[0].props.disabled).toBe(true);
  });

  it('reveals and describes only the invalid field that has been touched', () => {
    const render = createRenderer(AccessSection, {});
    let tree = render();
    getButton(tree, 'Hermes').props.onClick();
    tree = render();

    getInput(tree, 'bcn-access-hermes-manual-bot-name').props.onChange({
      target: { value: ' ' },
    });
    tree = render();

    const botNameInput = getInput(tree, 'bcn-access-hermes-manual-bot-name');
    expect(textOf(tree)).toContain('请输入 Bot 名称');
    expect(textOf(tree)).not.toContain('请输入 Profile 名称');
    expect(botNameInput.props['aria-invalid']).toBe(true);
    expect(botNameInput.props['aria-describedby']).toBe(
      'bcn-access-hermes-manual-bot-name-error',
    );
    expect(
      elementsIn(tree).some(
        (element) =>
          element.props.id === 'bcn-access-hermes-manual-bot-name-error',
      ),
    ).toBe(true);

    botNameInput.props.onChange({ target: { value: 'Hermes Reviewer' } });
    tree = render();
    getInput(tree, 'bcn-access-hermes-manual-profile').props.onChange({
      target: { value: ' ' },
    });
    tree = render();

    const profileInput = getInput(tree, 'bcn-access-hermes-manual-profile');
    expect(textOf(tree)).not.toContain('请输入 Bot 名称');
    expect(textOf(tree)).toContain('请输入 Profile 名称');
    expect(profileInput.props['aria-invalid']).toBe(true);
    expect(profileInput.props['aria-describedby']).toBe(
      'bcn-access-hermes-manual-profile-error',
    );
    expect(
      elementsIn(tree).some(
        (element) =>
          element.props.id === 'bcn-access-hermes-manual-profile-error',
      ),
    ).toBe(true);
  });

  it('renders valid Hermes values and preserves them across engine switches', () => {
    const render = createRenderer(AccessSection, {});
    let tree = render();
    getButton(tree, 'Hermes').props.onClick();
    tree = render();

    getInput(tree, 'bcn-access-hermes-manual-bot-name').props.onChange({
      target: { value: 'Hermes Reviewer' },
    });
    tree = render();
    getInput(tree, 'bcn-access-hermes-manual-profile').props.onChange({
      target: { value: 'review_bot-2' },
    });
    tree = render();

    expect(commandsIn(tree)[0]).toContain(
      "--bot-name 'Hermes Reviewer' --profile 'review_bot-2' --create-profile",
    );
    expect(getCopyButtons(tree)[0].props.disabled).toBe(false);

    getButton(tree, 'OpenClaw').props.onClick();
    tree = render();
    expect(commandsIn(tree)).toEqual([
      'openclaw manual registration-token',
      'openclaw automatic registration-token',
    ]);
    getButton(tree, 'Hermes').props.onClick();
    tree = render();

    expect(
      getInput(tree, 'bcn-access-hermes-manual-bot-name').props.value,
    ).toBe('Hermes Reviewer');
    expect(getInput(tree, 'bcn-access-hermes-manual-profile').props.value).toBe(
      'review_bot-2',
    );
  });
});
