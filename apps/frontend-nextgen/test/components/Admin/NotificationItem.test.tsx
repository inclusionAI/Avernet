/** @jest-environment jsdom */

import { NotificationItem } from '@/components/Admin/NotificationItem';
import type { NotificationSummary } from '@/domain/admin/models';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const item: NotificationSummary = {
  itemId: 'NOTIFICATION_1',
  itemType: 'NOTIFICATION',
  notificationId: 1,
  title: '群消息提醒',
  content: '群：测试群\n会话：新会话\n\nat 功能正常工作 ✅',
  gmtModified: '2026-09-20T12:00:00+08:00',
  bizType: 'SPACE',
  eventType: 'space.message',
  isRead: false,
};

describe('NotificationItem', () => {
  it('使用红色未读点，已读后不展示圆点', () => {
    const { rerender } = render(<NotificationItem item={item} />);
    expect(screen.getByLabelText('未读')).toHaveClass('bg-destructive');
    rerender(<NotificationItem item={{ ...item, isRead: true }} />);
    expect(screen.queryByLabelText('未读')).not.toBeInTheDocument();
  });

  it('时间与标题同排，并保留条目点击数据', () => {
    const onClick = jest.fn();
    const { container } = render(<NotificationItem item={item} onClick={onClick} />);
    const time = container.querySelector('time');
    expect(time).toHaveAttribute('dateTime', item.gmtModified);
    expect(time?.parentElement).toContainElement(screen.getByText(item.title));
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledWith(item);
  });

  it('预览保留正文换行语义，同时继续限制为两行', () => {
    render(<NotificationItem item={item} />);

    const content = screen.getByText((_, element) => element?.tagName === 'P' && element.textContent === item.content);
    expect(content).toHaveClass('whitespace-pre-wrap', 'break-words', 'line-clamp-2');
  });
});
