/** @jest-environment jsdom */
import { CreateGroupSessionModal } from '@/components/CollaborationSquare/CreateGroupSessionModal';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const group = { id: 'g1', name: '公开群' } as PublicGroup;

describe('CreateGroupSessionModal 消息视角 radio', () => {
  it('默认完整视角；选参与者视角后提交值携带 messageViewScope', () => {
    const onSubmit = jest.fn();
    render(<CreateGroupSessionModal open group={group} loading={false} onClose={jest.fn()} onSubmit={onSubmit} />);
    const radios = screen.getAllByRole('radio');
    expect(radios[0]).toBeChecked();
    fireEvent.change(screen.getByLabelText(/会话名称/), { target: { value: '会话A' } });
    fireEvent.change(screen.getByLabelText(/协作目标/), { target: { value: '目标B' } });
    fireEvent.click(radios[1]);
    fireEvent.click(screen.getByRole('button', { name: '创建会话' }));
    expect(onSubmit).toHaveBeenCalledWith({ title: '会话A', query: '目标B', messageViewScope: 'participant' });
  });
});
