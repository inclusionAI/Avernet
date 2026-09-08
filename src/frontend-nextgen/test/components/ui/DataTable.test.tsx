/** @jest-environment jsdom */

import { DataTable, DataTableCell, type DataTableCellProps, type DataTableColumn } from '@/components/ui/DataTable';
import '@testing-library/jest-dom';
import { createEvent, fireEvent, render, screen } from '@testing-library/react';

interface Row {
  id: string;
  name: string;
  count: number;
}

const rows: Row[] = [
  { id: 'r1', name: 'alpha', count: 1 },
  { id: 'r2', name: 'beta', count: 2 },
];

const columns: DataTableColumn<Row>[] = [
  { id: 'name', header: '名称', cell: (row) => row.name },
  { id: 'count', header: '数量', align: 'end', width: 'w-[96px]', cell: (row) => row.count },
];

const getRowKey = (row: Row) => row.id;

/** 第 0 行是表头,数据行从 1 开始。 */
function dataRows() {
  return screen.getAllByRole('row').slice(1);
}

describe('DataTable 渲染', () => {
  it('按列顺序渲染表头,数据行带 aria-rowindex(表头占 1)', () => {
    render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} />);

    expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['名称', '数量']);
    expect(dataRows()).toHaveLength(2);
    expect(dataRows()[0]).toHaveAttribute('aria-rowindex', '2');
    expect(dataRows()[1]).toHaveAttribute('aria-rowindex', '3');
    expect(screen.getByText('alpha')).toBeInTheDocument();
  });

  it('aria-label 落在 <table> 上,缺省为 DataTable', () => {
    const { rerender } = render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} />);
    expect(screen.getByRole('table', { name: 'DataTable' })).toBeInTheDocument();

    rerender(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} ariaLabel="Bot 列表" />);
    expect(screen.getByRole('table', { name: 'Bot 列表' })).toBeInTheDocument();
  });

  it('外层容器只负责 overflow-x-auto,不重复表格语义', () => {
    const { container } = render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} />);

    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper).toHaveClass('overflow-x-auto');
    expect(wrapper).not.toHaveAttribute('role');
    expect(screen.getAllByRole('table')).toHaveLength(1);
  });

  it('rowHeight 缺省 h-16,可覆盖', () => {
    const { rerender } = render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} />);
    expect(dataRows()[0]).toHaveClass('h-16');

    rerender(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} rowHeight="h-14" />);
    expect(dataRows()[0]).toHaveClass('h-14');
  });
});

describe('DataTable 空态', () => {
  it('无数据且传了 empty 时,渲染跨全部列的空态行', () => {
    render(<DataTable columns={columns} rows={[]} getRowKey={getRowKey} empty="暂无数据" />);

    const emptyCell = screen.getByText('暂无数据').closest('td');
    expect(emptyCell).toHaveAttribute('colspan', '2');
    expect(screen.getAllByRole('row')).toHaveLength(2);
    expect(screen.getAllByRole('columnheader')).toHaveLength(2);
  });

  it('无数据且未传 empty 时只渲染表头,不出现空态行', () => {
    render(<DataTable columns={columns} rows={[]} getRowKey={getRowKey} />);

    expect(screen.getAllByRole('row')).toHaveLength(1);
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('有数据时即使传了 empty 也不渲染空态', () => {
    render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} empty="暂无数据" />);

    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(3);
  });
});

describe('DataTable 行交互与键盘导航', () => {
  it('传 onRowClick 时行可聚焦、带手型,点击回调收到整行数据', () => {
    const onRowClick = jest.fn();
    render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} onRowClick={onRowClick} />);

    const firstRow = dataRows()[0];
    expect(firstRow).toHaveAttribute('tabindex', '0');
    // AGENTS.md 的兜底 cursor 选择器命中不了 <tr>,手型必须由 DataTableRow 自己写。
    expect(firstRow).toHaveClass('cursor-pointer');

    fireEvent.click(firstRow);
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });

  it('Enter 与 Space 都触发行点击,且 Space 被 preventDefault', () => {
    const onRowClick = jest.fn();
    render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} onRowClick={onRowClick} />);

    const firstRow = dataRows()[0];
    fireEvent.keyDown(firstRow, { key: 'Enter' });
    expect(onRowClick).toHaveBeenCalledTimes(1);

    const spaceEvent = createEvent.keyDown(firstRow, { key: ' ' });
    fireEvent(firstRow, spaceEvent);
    expect(spaceEvent.defaultPrevented).toBe(true);
    expect(onRowClick).toHaveBeenCalledTimes(2);
  });

  it('未传 onRowClick 时行不可聚焦、无手型,但保留 hover 反馈', () => {
    render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} />);

    const firstRow = dataRows()[0];
    expect(firstRow).not.toHaveAttribute('tabindex');
    expect(firstRow).not.toHaveClass('cursor-pointer');
    expect(firstRow).toHaveClass('hover:bg-muted/40');
    expect(firstRow).toHaveClass('transition-colors');
  });

  it('行不挂 role="button",保留表格 row 语义', () => {
    render(<DataTable columns={columns} rows={rows} getRowKey={getRowKey} onRowClick={() => {}} />);

    expect(dataRows()[0]).not.toHaveAttribute('role');
    expect(screen.getAllByRole('row')).toHaveLength(3);
  });
});

describe('DataTable 列对齐', () => {
  const alignColumns: DataTableColumn<Row>[] = [
    { id: 'a', header: 'A', cell: () => 'a' },
    { id: 'b', header: 'B', align: 'end', cell: () => 'b' },
    { id: 'c', header: 'C', align: 'center', cell: () => 'c' },
  ];

  it('align 同时作用于表头与单元格:start→left、end→right、center→center', () => {
    render(<DataTable columns={alignColumns} rows={[rows[0]]} getRowKey={getRowKey} />);

    const headers = screen.getAllByRole('columnheader');
    expect(headers[0]).toHaveClass('text-left');
    expect(headers[1]).toHaveClass('text-right');
    expect(headers[2]).toHaveClass('text-center');

    const cells = dataRows()[0].querySelectorAll('td');
    expect(cells[0]).toHaveClass('text-left');
    expect(cells[1]).toHaveClass('text-right');
    expect(cells[2]).toHaveClass('text-center');
  });

  it('headerAlign 只改表头,不影响单元格', () => {
    const cols: DataTableColumn<Row>[] = [
      { id: 'a', header: 'A', align: 'end', headerAlign: 'center', cell: () => 'a' },
    ];
    render(<DataTable columns={cols} rows={[rows[0]]} getRowKey={getRowKey} />);

    expect(screen.getByRole('columnheader')).toHaveClass('text-center');
    expect(dataRows()[0].querySelector('td')).toHaveClass('text-right');
  });

  it('width 与 className 透传到表头与单元格', () => {
    const cols: DataTableColumn<Row>[] = [
      { id: 'a', header: 'A', width: 'w-[96px]', className: 'shrink-0', cell: () => 'a' },
    ];
    render(<DataTable columns={cols} rows={[rows[0]]} getRowKey={getRowKey} />);

    expect(screen.getByRole('columnheader')).toHaveClass('w-[96px]');
    expect(screen.getByRole('columnheader')).toHaveClass('shrink-0');
    expect(dataRows()[0].querySelector('td')).toHaveClass('w-[96px]');
  });
});

/** <td> 必须挂在 table 结构里,否则触发 React validateDOMNesting 告警。 */
function CellHarness(props: DataTableCellProps) {
  return (
    <table>
      <tbody>
        <tr>
          <DataTableCell {...props} />
        </tr>
      </tbody>
    </table>
  );
}

describe('DataTableCell', () => {
  it('align 缺省 start,可覆盖为 end / center', () => {
    const { rerender } = render(<CellHarness>a</CellHarness>);
    expect(screen.getByText('a')).toHaveClass('text-left');

    rerender(<CellHarness align="end">a</CellHarness>);
    expect(screen.getByText('a')).toHaveClass('text-right');

    rerender(<CellHarness align="center">a</CellHarness>);
    expect(screen.getByText('a')).toHaveClass('text-center');
  });

  it('truncate 时套 max-w-0 并额外包一层 block truncate', () => {
    render(
      <CellHarness width="w-[120px]" truncate>
        a
      </CellHarness>,
    );

    const cell = screen.getByText('a').closest('td') as HTMLElement;
    expect(cell).toHaveClass('max-w-0');
    expect(cell).toHaveClass('truncate');
    expect(cell).toHaveClass('w-[120px]');
    expect(screen.getByText('a')).toHaveClass('block');
  });
});
