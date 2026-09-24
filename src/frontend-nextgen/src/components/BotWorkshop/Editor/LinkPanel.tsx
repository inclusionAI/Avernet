import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import { Textarea } from '@/components/ui/Textarea';
import type { BotLinkInput } from '@/domain/botLinks';
import { useBotLinks } from '@/hooks/useBotLinks';
import { useState } from 'react';
export function LinkPanel({ botId, editable }: { botId: string; editable: boolean }) {
  const links = useBotLinks(botId);
  const [type, setType] = useState<BotLinkInput['link_type']>('yuque');
  const [urls, setUrls] = useState('');
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    try {
      await links.add(
        urls
          .split(/\r?\n/)
          .map((url) => url.trim())
          .filter(Boolean)
          .map((url) => ({ url, name: '', link_type: type, access_modes: ['READ'] })),
      );
      setUrls('');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="space-y-3 border-t border-border p-5">
      <h2 className="text-sm font-semibold">关联链接</h2>
      {links.loading ? (
        <Spin tip="加载关联链接…" />
      ) : links.error ? (
        <Empty
          compact
          title="关联链接加载失败"
          description={links.error}
          action={
            <Button variant="outline" onClick={links.refresh}>
              重试
            </Button>
          }
        />
      ) : null}
      {links.items.map((link) => (
        <div className="space-y-2 rounded-md border border-border p-3" key={`${link.id}:${link.name}:${link.url}`}>
          <Input
            aria-label="链接名称"
            defaultValue={link.name}
            disabled={!editable}
            onBlur={(event) => {
              if (event.target.value !== link.name)
                void links.update(link.id, { name: event.target.value }).catch(() => undefined);
            }}
          />
          {editable ? (
            <Input
              aria-label="链接地址"
              defaultValue={link.url}
              onBlur={(event) => {
                if (event.target.value.trim() && event.target.value !== link.url)
                  void links.update(link.id, { url: event.target.value.trim() }).catch(() => undefined);
              }}
            />
          ) : null}
          {editable ? (
            <Select
              value={link.link_type}
              onValueChange={(value) =>
                void links.update(link.id, { link_type: value as BotLinkInput['link_type'] }).catch(() => undefined)
              }
            >
              <SelectTrigger aria-label="修改链接类型">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="yuque">语雀</SelectItem>
                <SelectItem value="dima">Dima</SelectItem>
                <SelectItem value="antcode">AntCode</SelectItem>
              </SelectContent>
            </Select>
          ) : null}
          <a
            className="block break-all text-xs text-primary"
            href={/^https?:\/\//i.test(link.url) ? link.url : undefined}
            target="_blank"
            rel="noreferrer"
          >
            {link.url}
          </a>
          <div className="flex gap-2">
            {link.link_type === 'yuque' ? (
              <Button
                size="sm"
                variant="outline"
                disabled={!editable}
                onClick={() =>
                  void links
                    .update(link.id, {
                      access_modes: link.access_modes?.includes('WRITE') ? ['READ'] : ['READ', 'WRITE'],
                    })
                    .catch(() => undefined)
                }
              >
                {link.access_modes?.includes('WRITE') ? '读写权限' : '只读权限'}
              </Button>
            ) : null}
            <ConfirmDialog
              title="删除关联链接"
              description={`确认删除「${link.name}」？`}
              disabled={!editable}
              onConfirm={() => links.remove(link.id)}
              confirmVariant="destructive"
            >
              <Button variant="ghost" size="sm">
                删除
              </Button>
            </ConfirmDialog>
          </div>
        </div>
      ))}
      {editable ? (
        <div className="space-y-2">
          <Select value={type} onValueChange={(value) => setType(value as BotLinkInput['link_type'])}>
            <SelectTrigger aria-label="链接类型">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="yuque">语雀</SelectItem>
              <SelectItem value="dima">Dima</SelectItem>
              <SelectItem value="antcode">AntCode</SelectItem>
            </SelectContent>
          </Select>
          <Textarea
            value={urls}
            onChange={(event) => setUrls(event.target.value)}
            placeholder="每行一个链接"
            aria-label="待添加链接"
          />
          <Button disabled={busy || !urls.trim()} onClick={() => void add().catch(() => undefined)}>
            批量添加
          </Button>
        </div>
      ) : null}
    </section>
  );
}
