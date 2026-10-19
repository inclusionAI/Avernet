import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Pagination } from '@/components/ui/Pagination';
import { Spin } from '@/components/ui/Spin';
import { Textarea } from '@/components/ui/Textarea';
import { history } from '@umijs/max';
import { Plus, Search } from 'lucide-react';
import { useCallback, useState } from 'react';
import { ErrorBox, Field, Hint, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

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
  return (
    <div className="space-y-4">
      <ErrorBox message={identity.error ?? domains.error} retry={domains.reload} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="relative w-[260px] max-w-full">
          <Search size={14} className="pointer-events-none absolute left-3 top-2.5 text-muted-foreground" />
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
        <Spin tip="加载 Domain…" />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[900px] table-fixed text-left text-sm">
              <colgroup>
                <col />
                <col style={{ width: 180 }} />
                <col style={{ width: 100 }} />
                <col style={{ width: 110 }} />
                <col />
                <col style={{ width: 140 }} />
              </colgroup>
              <thead className="bg-muted">
                <tr>
                  {['Domain 名称', 'Domain ID', '工号', '模板数量', '描述', '操作'].map((v) => (
                    <th className="whitespace-nowrap px-4 py-3 text-xs font-medium" key={v}>
                      {v}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.slice((page - 1) * 10, page * 10).map((d) => (
                  <tr className="border-t border-border" key={`${d.ownerUserId}/${d.domainId}`}>
                    <td className="p-4 font-medium">
                      <Hint content={d.name}>
                        <div className="truncate">{d.name}</div>
                      </Hint>
                    </td>
                    <td className="p-4">
                      <Hint content={d.domainId}>
                        <div className="truncate font-mono text-xs text-muted-foreground">{d.domainId}</div>
                      </Hint>
                    </td>
                    <td className="whitespace-nowrap p-4 text-xs text-muted-foreground">{d.ownerUserId}</td>
                    <td className="whitespace-nowrap p-4">
                      <span className="rounded bg-muted px-2 py-1 text-xs">{d.templateCount} 个模板</span>
                    </td>
                    <td className="p-4">
                      <Hint content={d.description || undefined}>
                        <div className="truncate text-xs text-muted-foreground">{d.description || '—'}</div>
                      </Hint>
                    </td>
                    <td className="whitespace-nowrap p-4">
                      <Button
                        className="whitespace-nowrap px-0 text-primary"
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          history.push(
                            `/evolution-factory/domains/${encodeURIComponent(d.ownerUserId)}/${encodeURIComponent(
                              d.domainId,
                            )}`,
                          )
                        }
                      >
                        查看模板与运行
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!items.length && <Empty title="暂无评测模板" />}
          <Pagination current={page} pageSize={10} total={items.length} onChange={setPage} />
        </>
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
