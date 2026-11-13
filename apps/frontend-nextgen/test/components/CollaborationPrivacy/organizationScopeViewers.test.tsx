/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { RelationCard } from '@/components/CollaborationPrivacy/RelationCard';
import { RequestList } from '@/components/CollaborationPrivacy/RequestList';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

afterEach(() => extendCapabilities(defaultCapabilities));

it.each([false, true])('historical organization viewers respect directory availability (%s)', (enabled) => {
  extendCapabilities({ getOrganizationDirectoryEnabled: () => ({ status: 'available', value: enabled }) });
  render(
    <>
      <RelationCard
        audience="bot"
        config={{ scope: 'restricted', organizationPaths: [['示例集团']] }}
        onEdit={jest.fn()}
        onViewScope={jest.fn()}
      />
      <RequestList
        config={{ mode: 'partial_exempt', exemptOrganizationPaths: [['示例集团']] }}
        disabled={false}
        onEdit={jest.fn()}
        onViewScope={jest.fn()}
      />
    </>,
  );
  expect(screen.queryAllByRole('button', { name: '查看' })).toHaveLength(enabled ? 2 : 0);
  expect(screen.getByText(/1 个组织范围/)).toBeInTheDocument();
  expect(screen.getByText(/1 个免审批范围/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '编辑策略' })).toBeEnabled();
});
