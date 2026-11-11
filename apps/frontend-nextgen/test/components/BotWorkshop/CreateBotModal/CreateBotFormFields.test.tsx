/** @jest-environment jsdom */

import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { CreateBotFormFields } from '@/components/BotWorkshop/CreateBotModal/CreateBotFormFields';
import type { BotCreateInput } from '@/services/botWorkshop';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach } from '@jest/globals';

afterEach(() => {
  extendCapabilities({ getBotEngineOptions: defaultCapabilities.getBotEngineOptions });
});

const aicodingInput = {
  scenario: 'cloud',
  name: '重复仓库 Bot',
  description: '',
  engine: 'aicoding',
  spaceId: '1',
  ownership: 'team',
  serviceMode: 'non-service',
  initialize: true,
  avatarUrl: '',
  agentCoding: {
    kind: 'template',
    template: { key: 'template' } as AgentCodingTemplate,
    values: {},
  },
} as BotCreateInput;

const setValues = jest.fn() as unknown as React.Dispatch<React.SetStateAction<BotCreateInput>>;

function renderFields(
  values: BotCreateInput,
  agentCodingError?: string,
  error?: string,
  onErrorChange?: (error?: string) => void,
) {
  return render(
    <CreateBotFormFields
      values={values}
      setValues={setValues}
      spaces={[{ id: '1', name: '团队空间', ownership: 'team', canCreate: true }]}
      creating={false}
      error={error}
      onErrorChange={onErrorChange}
      agentCodingError={agentCodingError}
      agentCodingTemplates={[]}
      onValidateReady={jest.fn()}
      onAgentCodingErrorChange={jest.fn()}
      onCancel={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );
}

test('AgentCoding 存在阻塞校验错误时禁用创建按钮', () => {
  extendCapabilities({
    getBotEngineOptions: () => ({
      status: 'available',
      value: [{ value: 'aicoding', label: 'AgentCoding', createPanel: 'agent-coding' }],
    }),
  });

  renderFields(aicodingInput, '代码仓库字段存在重复的仓库地址');

  expect(screen.getByRole('button', { name: '创建云端 Bot' })).toBeDisabled();
});

test('普通引擎不受 AgentCoding 阻塞错误影响', () => {
  extendCapabilities({
    getBotEngineOptions: () => ({
      status: 'available',
      value: [
        { value: 'openclaw', label: 'OpenClaw' },
        { value: 'aicoding', label: 'AgentCoding', createPanel: 'agent-coding' },
      ],
    }),
  });

  renderFields({ ...aicodingInput, engine: 'openclaw', agentCoding: undefined }, '重复仓库');

  expect(screen.getByRole('button', { name: '创建云端 Bot' })).toBeEnabled();
});
test('Bot 名称修正后清理旧的提交错误，不再遮挡 AgentCoding 校验提示', async () => {
  extendCapabilities({
    getBotEngineOptions: () => ({
      status: 'available',
      value: [{ value: 'aicoding', label: 'AgentCoding', createPanel: 'agent-coding' }],
    }),
  });
  const onErrorChange = jest.fn();
  const { rerender } = renderFields(
    {...aicodingInput, name: ''},
    undefined,
    '请输入 Bot 名称',
    onErrorChange,
  );

  const nameInput = screen.getByLabelText(/Bot 名称/);
  expect(nameInput).toHaveAttribute('aria-invalid', 'true');

  fireEvent.change(nameInput, { target: { value: '测试 Bot' } });
  expect(onErrorChange).toHaveBeenCalledWith(undefined);

  // 状态由 CreateBotModal 持有：姓名 onChange 清理提交错误，values 随父层重渲染更新。
  rerender(
    <CreateBotFormFields
      values={{ ...aicodingInput, name: '测试 Bot' }}
      setValues={setValues}
      spaces={[{ id: '1', name: '团队空间', ownership: 'team', canCreate: true }]}
      creating={false}
      onErrorChange={onErrorChange}
      agentCodingTemplates={[]}
      onValidateReady={jest.fn()}
      onAgentCodingErrorChange={jest.fn()}
      onCancel={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );
  expect(nameInput).toHaveAttribute('aria-invalid', 'false');

  rerender(
    <CreateBotFormFields
      values={{ ...aicodingInput, name: '测试 Bot' }}
      setValues={setValues}
      spaces={[{ id: '1', name: '团队空间', ownership: 'team', canCreate: true }]}
      creating={false}
      onErrorChange={onErrorChange}
      agentCodingError="请填写前端代码仓库"
      agentCodingTemplates={[]}
      onValidateReady={jest.fn()}
      onAgentCodingErrorChange={jest.fn()}
      onCancel={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  expect(await screen.findByText('请填写前端代码仓库')).toBeInTheDocument();
});
