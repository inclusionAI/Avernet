/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { CodingModelConfigField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingModelConfigField';
import '@testing-library/jest-dom';
import { afterEach } from '@jest/globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

afterEach(() => {
  extendCapabilities({ getHumanIdentity: defaultCapabilities.getHumanIdentity });
});

function codefuseToken(userId: string) {
  return btoa(JSON.stringify({ w: userId }));
}

test('填写当前登录人的 CodeFuse Token 不再误报无法获取登录人', async () => {
  extendCapabilities({
    getHumanIdentity: () => ({
      status: 'available',
      value: { userId: '12345', displayName: '测试用户', avatarUrl: '', online: true },
    }),
  });
  const onValidationChange = jest.fn();

  render(
    <CodingModelConfigField
      initialConfig={{
        model: 'codefuse-model',
        runtime: 'codefuse-antcc',
        support_engines: ['codefuse-antcc'],
      }}
      onChange={jest.fn()}
      onValidationChange={onValidationChange}
    />,
  );

  fireEvent.change(screen.getByPlaceholderText('粘贴授权 Token'), {
    target: { value: codefuseToken('12345') },
  });

  await waitFor(() =>
    expect(screen.queryByText('无法获取当前登录人，请刷新页面后重试')).not.toBeInTheDocument(),
  );
  await waitFor(() => expect(onValidationChange).toHaveBeenLastCalledWith(null));
});

test('填写其他登录人的 CodeFuse Token 会上报但不重复渲染输入框下方提示', async () => {
  extendCapabilities({
    getHumanIdentity: () => ({
      status: 'available',
      value: { userId: '12345', displayName: '测试用户', avatarUrl: '', online: true },
    }),
  });
  const onValidationChange = jest.fn();

  render(
    <CodingModelConfigField
      initialConfig={{
        model: 'codefuse-model',
        runtime: 'codefuse-antcc',
        support_engines: ['codefuse-antcc'],
      }}
      onChange={jest.fn()}
      onValidationChange={onValidationChange}
    />,
  );

  fireEvent.change(screen.getByPlaceholderText('粘贴授权 Token'), {
    target: { value: codefuseToken('99999') },
  });

  expect(
    await screen.findByPlaceholderText('粘贴授权 Token'),
  ).toHaveAttribute('aria-invalid', 'true');
  await waitFor(() =>
    expect(onValidationChange).toHaveBeenLastCalledWith(
      '授权 Token 与当前登录人不一致，请使用当前账号的 CodeFuse Token',
    ),
  );
  expect(
    screen.queryByText('授权 Token 与当前登录人不一致，请使用当前账号的 CodeFuse Token'),
  ).not.toBeInTheDocument();
});
test('CodeFuse Token 为空时不重复渲染输入框下方提示', async () => {
  extendCapabilities({
    getHumanIdentity: () => ({
      status: 'available',
      value: { userId: '12345', displayName: '测试用户', avatarUrl: '', online: true },
    }),
  });
  const onValidationChange = jest.fn();

  render(
    <CodingModelConfigField
      initialConfig={{
        model: 'codefuse-model',
        runtime: 'codefuse-antcc',
        support_engines: ['codefuse-antcc'],
      }}
      onChange={jest.fn()}
      onValidationChange={onValidationChange}
    />,
  );

  const input = screen.getByLabelText('授权 Token');
  expect(input).toHaveAttribute('aria-invalid', 'true');
  await waitFor(() =>
    expect(onValidationChange).toHaveBeenLastCalledWith('选择了 CodeFuse 模型，请填写授权 Token'),
  );
  expect(screen.queryByText('选择了 CodeFuse 模型，请填写授权 Token')).not.toBeInTheDocument();
});
