import { useExt } from '@/capabilities';
import { HermesBotConfigFields } from '@/pages/BcnHome/components/HermesBotConfigFields';
import { useRegisterToken } from '@/pages/BcnHome/hooks/useRegisterToken';
import { HERMES_MULTI_PROFILE_NOTICE } from '@/pages/BcnHome/lib/botAccess';
import React from 'react';
import AddBotGuideModal from './AddBotGuideModal';

jest.mock('@/capabilities', () => ({
  ...jest.requireActual('@/capabilities'),
  useExt: jest.fn(),
}));
jest.mock('@/pages/BcnHome/hooks/useRegisterToken', () => ({
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
    useEffect() {
      hookIndex++;
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

function getCopyButton(tree: TestElement): TestElement {
  const button = elementsIn(tree).find(
    (element) => element.type === 'button' && element.props.title === '复制',
  );
  if (!button) throw new Error('Copy button not found');
  return button;
}

function commandIn(tree: TestElement): string {
  const command = elementsIn(tree).find((element) => element.type === 'code');
  if (!command) throw new Error('Command not found');
  return textOf(command);
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

describe('AddBotGuideModal Hermes configuration', () => {
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
    const render = createRenderer(AddBotGuideModal, {
      open: true,
      onOpenChange: jest.fn(),
    });
    let tree = render();

    expect(commandIn(tree)).toBe('openclaw manual registration-token');
    expect(getCopyButton(tree).props.disabled).toBe(false);

    getButton(tree, 'Hermes').props.onClick();
    tree = render();

    expect(textOf(tree)).not.toContain('请输入 Bot 名称');
    expect(textOf(tree)).not.toContain('请输入 Profile 名称');
    expect(
      getInput(tree, 'add-bot-guide-hermes-manual-bot-name').props[
        'aria-describedby'
      ],
    ).toBeUndefined();
    expect(
      getInput(tree, 'add-bot-guide-hermes-manual-profile').props[
        'aria-describedby'
      ],
    ).toBeUndefined();
    expect(getCopyButton(tree).props.disabled).toBe(true);
    expect(commandsIn(tree)).toEqual([]);
    expect(textOf(tree)).toContain('请先填写有效的 Bot 名称和 Profile。');
    expect(countText(tree, HERMES_MULTI_PROFILE_NOTICE)).toBe(1);

    getInput(tree, 'add-bot-guide-hermes-manual-bot-name').props.onChange({
      target: { value: 'Hermes Reviewer' },
    });
    tree = render();
    getInput(tree, 'add-bot-guide-hermes-manual-profile').props.onChange({
      target: { value: 'INVALID_PROFILE' },
    });
    tree = render();

    expect(commandsIn(tree)).toEqual([]);
    expect(textOf(tree)).not.toContain('--profile');
    expect(getCopyButton(tree).props.disabled).toBe(true);

    getButton(tree, 'Bot 自动接入').props.onClick();
    tree = render();
    expect(commandIn(tree)).toBe('hermes automatic registration-token');
    expect(getCopyButton(tree).props.disabled).toBe(false);
    expect(countText(tree, HERMES_MULTI_PROFILE_NOTICE)).toBe(1);
  });

  it('reveals and describes only the invalid field that has been touched', () => {
    const render = createRenderer(AddBotGuideModal, {
      open: true,
      onOpenChange: jest.fn(),
    });
    let tree = render();
    getButton(tree, 'Hermes').props.onClick();
    tree = render();

    getInput(tree, 'add-bot-guide-hermes-manual-bot-name').props.onChange({
      target: { value: ' ' },
    });
    tree = render();

    const botNameInput = getInput(tree, 'add-bot-guide-hermes-manual-bot-name');
    expect(textOf(tree)).toContain('请输入 Bot 名称');
    expect(textOf(tree)).not.toContain('请输入 Profile 名称');
    expect(botNameInput.props['aria-invalid']).toBe(true);
    expect(botNameInput.props['aria-describedby']).toBe(
      'add-bot-guide-hermes-manual-bot-name-error',
    );
    expect(
      elementsIn(tree).some(
        (element) =>
          element.props.id === 'add-bot-guide-hermes-manual-bot-name-error',
      ),
    ).toBe(true);

    botNameInput.props.onChange({ target: { value: 'Hermes Reviewer' } });
    tree = render();
    getInput(tree, 'add-bot-guide-hermes-manual-profile').props.onChange({
      target: { value: ' ' },
    });
    tree = render();

    const profileInput = getInput(tree, 'add-bot-guide-hermes-manual-profile');
    expect(textOf(tree)).not.toContain('请输入 Bot 名称');
    expect(textOf(tree)).toContain('请输入 Profile 名称');
    expect(profileInput.props['aria-invalid']).toBe(true);
    expect(profileInput.props['aria-describedby']).toBe(
      'add-bot-guide-hermes-manual-profile-error',
    );
    expect(
      elementsIn(tree).some(
        (element) =>
          element.props.id === 'add-bot-guide-hermes-manual-profile-error',
      ),
    ).toBe(true);
  });

  it('renders valid Hermes values and preserves them across method and engine switches', () => {
    const render = createRenderer(AddBotGuideModal, {
      open: true,
      onOpenChange: jest.fn(),
    });
    let tree = render();
    getButton(tree, 'Hermes').props.onClick();
    tree = render();

    getInput(tree, 'add-bot-guide-hermes-manual-bot-name').props.onChange({
      target: { value: 'Hermes Reviewer' },
    });
    tree = render();
    getInput(tree, 'add-bot-guide-hermes-manual-profile').props.onChange({
      target: { value: 'review_bot-2' },
    });
    tree = render();

    expect(commandIn(tree)).toContain(
      "--bot-name 'Hermes Reviewer' --profile 'review_bot-2' --create-profile",
    );
    expect(getCopyButton(tree).props.disabled).toBe(false);

    getButton(tree, 'Bot 自动接入').props.onClick();
    tree = render();
    expect(commandIn(tree)).toBe('hermes automatic registration-token');
    expect(textOf(tree)).not.toContain('--create-profile');

    getButton(tree, 'OpenClaw').props.onClick();
    tree = render();
    expect(commandIn(tree)).toBe('openclaw automatic registration-token');
    getButton(tree, '用户自助接入').props.onClick();
    tree = render();
    expect(commandIn(tree)).toBe('openclaw manual registration-token');

    getButton(tree, 'Hermes').props.onClick();
    tree = render();
    expect(
      getInput(tree, 'add-bot-guide-hermes-manual-bot-name').props.value,
    ).toBe('Hermes Reviewer');
    expect(
      getInput(tree, 'add-bot-guide-hermes-manual-profile').props.value,
    ).toBe('review_bot-2');
  });
});
