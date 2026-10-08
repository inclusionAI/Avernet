/** @jest-environment jsdom */

import { NotificationItem } from '@/components/Admin/NotificationItem';
import type { NotificationSummary } from '@/domain/admin/models';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

const item: NotificationSummary = {
  itemId: 'NOTIFICATION_1',
  itemType: 'NOTIFICATION',
  notificationId: 1,
  title: '群消息提醒',
  content: '群：测试群\n会话：新会话\n\nat 功能正常工作 ✅',
  gmtModified: '2026-09-20T12:00:00+08:00',
  isRead: false,
};

describe('NotificationItem', () => {
  it('预览保留正文换行语义，同时继续限制为两行', () => {
    render(<NotificationItem item={item} />);

    const content = screen.getByText((_, element) => element?.tagName === 'P' && element.textContent === item.content);
    expect(content).toHaveClass('whitespace-pre-wrap', 'break-words', 'line-clamp-2');
  });
});
