import { Button } from '@/components/ui/Button';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Pagination } from '@/components/ui/Pagination';
import { Textarea } from '@/components/ui/Textarea';
import type { Domain } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { Plus, Search } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { ErrorBox, Field, Hint, ListSkeleton, PlaceholderPanel, TablePanel, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

const linkClass = 'h-7 rounded-md px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary';

const COLUMN_WIDTHS = [
  'w-[clamp(200px,18vw,280px)]',
  'w-[clamp(160px,14vw,200px)]',
  'w-[clamp(100px,9vw,120px)]',
  'w-[clamp(104px,9vw,128px)]',
  'w-[clamp(180px,16vw,260px)]',
  'w-[150px]',
];

export default function DomainList() {
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
  const domains = useResource(
    useCallback(() => evolutionApi.domains(), [identity.key]),
    identity.ready,
  );
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [id, setId] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const items =
    domains.data?.filter((d) =>
      `${d.name} ${d.domainId} ${d.ownerUserId}`.toLowerCase().includes(search.toLowerCase()),
    ) ?? [];
  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!/^[a-z0-9_-]+$/.test(id) || !name.trim()) {
      setError('请输入名称；Domain ID 仅含小写字母、数字、下划线和连字符');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await evolutionApi.createDomain({ domainId: id, name: name.trim(), description });
      setOpen(false);
      setName('');
      setId('');
      setDescription('');
      domains.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败');
    } finally {
      setBusy(false);
    }
  };
  const columns = useMemo<DataTableColumn<Domain>[]>(
    () => [
      {
        id: 'name',
        header: 'Domain 名称',
        width: COLUMN_WIDTHS[0],
        cell: (d) => (
          <Hint content={d.name}>
            <span className="block truncate text-sm font-medium text-foreground">{d.name}</span>
          </Hint>
        ),
      },
      {
        id: 'domainId',
        header: 'Domain ID',
        width: COLUMN_WIDTHS[1],
        cell: (d) => (
          <Hint content={d.domainId}>
            <span className="block truncate font-mono text-xs text-content-icon">{d.domainId}</span>
          </Hint>
        ),
      },
      {
        id: 'owner',
        header: '工号',
        width: COLUMN_WIDTHS[2],
        cell: (d) => <span className="text-[13px] text-muted-foreground">{d.ownerUserId}</span>,
      },
      {
        id: 'count',
        header: '模板数量',
        width: COLUMN_WIDTHS[3],
        cell: (d) => (
          <span className="inline-flex items-center rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
            {d.templateCount} 个模板
          </span>
        ),
      },
      {
        id: 'description',
        header: '描述',
        width: COLUMN_WIDTHS[4],
        cell: (d) => (
          <Hint content={d.description || undefined}>
            <span className="block truncate text-xs text-muted-foreground">{d.description || '—'}</span>
          </Hint>
        ),
      },
      {
        id: 'actions',
        header: '操作',
        width: COLUMN_WIDTHS[5],
        align: 'end',
        className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
        headerClassName: 'sticky right-0 z-10 bg-muted/50',
        cell: (d) => (
          <Button
            type="button"
            variant="ghost"
            className={linkClass}
            onClick={() => {
              Tracert.click(Points.evolution.openDomain, { domain_id: d.domainId });
              history.push(
                `/evolution-factory/domains/${encodeURIComponent(d.ownerUserId)}/${encodeURIComponent(d.domainId)}`,
              );
            }}
          >
            查看模板与运行
          </Button>
        ),
      },
    ],
    [],
  );
  return (
    <div className="min-w-0">
      <ErrorBox message={identity.error ?? domains.error} retry={domains.reload} />
      {/* 筛选行：搜索 + 右侧新建，与 Bot 管理工具行同 idiom。 */}
      <div className="mb-4 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-3">
        <div className="relative w-full sm:w-[260px] sm:shrink-0">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索 Domain"
            placeholder="搜索名称、Domain ID 或工号"
            className="pl-9"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <Button
          type="button"
          className="ml-auto"
          leftIcon={<Plus size={14} />}
          onClick={() => {
            setError('');
            setOpen(true);
          }}
          disabled={!identity.ready}
        >
          新建 Domain
        </Button>
      </div>
      {domains.loading ? (
        <TablePanel>
          <ListSkeleton widths={COLUMN_WIDTHS} ariaLabel="Domain 列表加载中" />
        </TablePanel>
      ) : domains.data && items.length ? (
        <>
          <TablePanel>
            <DataTable
              columns={columns}
              rows={items.slice((page - 1) * 10, page * 10)}
              getRowKey={(d) => `${d.ownerUserId}/${d.domainId}`}
              rowHeight="h-18"
              ariaLabel="Domain 列表"
              minWidth="min-w-[1000px]"
            />
          </TablePanel>
          <Pagination current={page} pageSize={10} total={items.length} onChange={setPage} layout="between" className="mt-4" />
        </>
      ) : (
        domains.data && (
          <PlaceholderPanel>
            <Empty
              title={search ? '没有符合条件的 Domain' : '暂无评测模板'}
              description={search ? '尝试更换关键词或清除筛选。' : '新建一个 Domain，沉淀训练与测试评测模板。'}
            />
          </PlaceholderPanel>
        )
      )}
      <Modal
        open={open}
        onOpenChange={(v) => {
          if (!busy) setOpen(v);
        }}
      >
        <ModalContent className="max-w-[560px]" aria-describedby={undefined}>
          <ModalHeader>
            <ModalTitle className="text-base">新建 Domain</ModalTitle>
          </ModalHeader>
          <form onSubmit={create} className="space-y-4">
            <ErrorBox message={error} />
            <Field label="Domain ID *">
              <Input
                required
                placeholder="小写字母、数字、下划线或连字符"
                value={id}
                onChange={(e) => setId(e.target.value)}
              />
            </Field>
            <Field label="名称 *">
              <Input required placeholder="请输入 Domain 名称" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="描述">
              <Textarea
                rows={3}
                placeholder="请输入描述（可选）"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </Field>
            <div className="flex justify-end gap-2 border-t border-border pt-4">
              <Button type="button" variant="outline" disabled={busy} onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button type="submit" loading={busy}>
                确定
              </Button>
            </div>
          </form>
        </ModalContent>
      </Modal>
    </div>
  );
}