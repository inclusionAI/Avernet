/** @jest-environment jsdom */
import LegacyPrivateChatRedirect from '@/pages/LegacyPrivateChatRedirect';
import { history } from '@umijs/max';
import { render } from '@testing-library/react';

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn() },
  useLocation: () => ({ search: '?id=20260407_jmg31orw%3A152819' }),
}));

const mockedReplace = history.replace as unknown as jest.Mock;

describe('LegacyPrivateChatRedirect', () => {
  it('老路径兜底重定向到 /workspace/chat 并原样透传 query', () => {
    render(<LegacyPrivateChatRedirect />);
    expect(mockedReplace).toHaveBeenCalledWith('/workspace/chat?id=20260407_jmg31orw%3A152819');
  });
});
