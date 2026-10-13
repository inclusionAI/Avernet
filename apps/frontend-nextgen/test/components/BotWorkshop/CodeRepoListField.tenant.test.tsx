/** @jest-environment jsdom */
import { CodeRepoListField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/aicoding/configFields';
import type { CodeRepositoryTenant } from '@/services/botWorkshop/agentCodingLegacyService';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { useState } from 'react';

jest.mock('@/services/botWorkshop/agentCodingLegacyService', () => ({
  searchAntCodeProjects: jest.fn(),
}));

import { searchAntCodeProjects } from '@/services/botWorkshop/agentCodingLegacyService';

const searchMock = searchAntCodeProjects as jest.Mock;

function Harness() {
  const [tenant, setTenant] = useState<CodeRepositoryTenant>('ant');
  return (
    <CodeRepoListField
      label="后端代码仓库"
      value={[{ repo_url: 'https://ant-code.test/selected/repo' }]}
      onChange={jest.fn()}
      tenant={tenant}
      onTenantChange={setTenant}
    />
  );
}

describe('CodeRepoListField tenant switch', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    searchMock.mockReset();
  });

  it('切换到消金后清空主站候选，并用消金租户继续搜索', async () => {
    searchMock
      .mockResolvedValueOnce([
        {
          name: '主站仓库',
          web_url: 'https://ant-code.test/ant/repo',
          accessLevel: 30,
        },
      ])
      .mockResolvedValueOnce([
        {
          name: '消金仓库',
          web_url: 'https://xiaojin-code.test/xiaojin/repo',
          accessLevel: 30,
        },
      ]);

    render(<Harness />);
    const input = screen.getByPlaceholderText('输入项目名称搜索...');

    await act(async () => {
      fireEvent.change(input, { target: { value: 'repo' } });
      jest.advanceTimersByTime(300);
    });

    expect(searchMock).toHaveBeenCalledWith('repo', { tenant: 'ant' });
    expect(screen.getByText('主站仓库')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '消金' }));
    });
    expect(screen.queryByText('主站仓库')).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.change(input, { target: { value: 'xiaojin-repo' } });
      jest.advanceTimersByTime(300);
    });

    expect(searchMock).toHaveBeenLastCalledWith('xiaojin-repo', { tenant: 'xiaojin' });
    expect(screen.getByText('消金仓库')).toBeInTheDocument();
    expect(screen.getByText('https://xiaojin-code.test/xiaojin/repo')).toBeInTheDocument();
  });
});
