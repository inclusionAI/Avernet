import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import type { BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { Check, ExternalLink, FolderUp, Plug, Search, Shapes } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

type Source = 'mine' | 'market' | 'workshop';
type PickerItem = BotEditorSkill | BotEditorMcp;

interface CapabilityPickerModalProps {
  kind: 'skill' | 'mcp';
  open: boolean;
  marketItems: PickerItem[];
  workshopItems: PickerItem[];
  myItems: PickerItem[];
  existingIds: string[];
  onOpenChange: (open: boolean) => void;
  onConfirm: (ids: string[]) => Promise<void>;
  onUploadFolder?: (files: File[]) => Promise<BotEditorSkill>;
}

const itemId = (item: PickerItem) => ('serverCode' in item ? item.serverCode : item.id);

export function CapabilityPickerModal({
  kind,
  open,
  marketItems,
  workshopItems,
  myItems,
  existingIds,
  onOpenChange,
  onConfirm,
  onUploadFolder,
}: CapabilityPickerModalProps) {
  const [source, setSource] = useState<Source>('market');
  const [keyword, setKeyword] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const folderRef = useRef<HTMLInputElement>(null);
  const items = source === 'mine' ? myItems : source === 'market' ? marketItems : workshopItems;
  const visibleItems = useMemo(() => {
    const normalized = keyword.trim().toLowerCase();
    return items.filter((item) => {
      if (existingIds.includes(itemId(item))) return false;
      return !normalized || `${item.name} ${item.description ?? ''}`.toLowerCase().includes(normalized);
    });
  }, [existingIds, items, keyword]);
  const close = () => {
    setSelected([]);
    setKeyword('');
    onOpenChange(false);
  };
  const submit = async () => {
    setSubmitting(true);
    try {
      await onConfirm(selected);
      close();
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <Modal open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>添加 {kind === 'skill' ? 'Skill' : 'MCP'}</ModalTitle>
          <ModalDescription>从市场或能力工坊选择，可一次添加多个能力。</ModalDescription>
        </ModalHeader>
        <Segmented
          value={source}
          onChange={(value) => {
            setSource(value);
            setSelected([]);
          }}
          options={[
            { value: 'market', label: kind === 'skill' ? '引用市场 Skill' : '引用市场 MCP' },
            {
              value: 'workshop',
              label: kind === 'skill' ? '引用工坊 Skill' : '引用工坊 MCP',
              disabledReason: kind === 'mcp' ? '后端暂未提供能力工坊 MCP 独立列表 OpenAPI' : undefined,
            },
            ...(kind === 'skill' ? [{ value: 'mine' as const, label: '我的 Skill' }] : []),
          ]}
          className="w-fit"
        />
        {kind === 'skill' && source === 'mine' && onUploadFolder ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3">
            <p className="m-0 min-w-0 flex-1 text-xs text-muted-foreground">
              本地目录上传后会出现在“我的 Skill”，勾选并确认后才加入当前能力集。
            </p>
            <input
              ref={folderRef}
              hidden
              type="file"
              multiple
              {...{ webkitdirectory: '' }}
              onChange={(event) => {
                const files = Array.from(event.target.files ?? []);
                if (files.length) {
                  setUploading(true);
                  void onUploadFolder(files)
                    .then((skill) => setSelected((current) => [...new Set([...current, skill.id])]))
                    .catch(() => undefined)
                    .finally(() => setUploading(false));
                }
                event.target.value = '';
              }}
            />
            <Button
              variant="secondary"
              leftIcon={<FolderUp className="size-4" />}
              disabled={uploading}
              onClick={() => folderRef.current?.click()}
            >
              {uploading ? '上传中…' : '上传本地目录'}
            </Button>
          </div>
        ) : null}
        <div className="relative">
          <Search aria-hidden className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder={`搜索${source === 'mine' ? '我的' : source === 'market' ? '市场' : '能力工坊'}中的 ${
              kind === 'skill' ? 'Skill' : 'MCP'
            }`}
          />
        </div>
        <div className="app-scrollbar grid max-h-[420px] grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2">
          {visibleItems.length ? (
            visibleItems.map((item) => {
              const id = itemId(item);
              const active = selected.includes(id);
              return (
                <Button
                  key={id}
                  variant="secondary"
                  className={`h-auto min-h-24 items-start justify-start whitespace-normal p-3 text-left ${
                    active ? 'border-primary bg-accent' : ''
                  }`}
                  onClick={() =>
                    setSelected((current) => (active ? current.filter((value) => value !== id) : [...current, id]))
                  }
                >
                  <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted">
                    {kind === 'skill' ? <Shapes className="size-4" /> : <Plug className="size-4" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-xs font-medium">{item.name}</span>
                      {'version' in item && item.version ? <Badge>{item.version}</Badge> : null}
                    </span>
                    <span className="mt-1 line-clamp-2 text-xs font-normal text-muted-foreground">
                      {item.description || '暂无描述'}
                    </span>
                    {kind === 'skill' ? (
                      <span className="mt-2 inline-flex items-center gap-1 text-xs text-primary">
                        <ExternalLink className="size-3" /> 查看 Skill 详情
                      </span>
                    ) : null}
                  </span>
                  {active ? <Check className="size-4 shrink-0 text-primary" /> : null}
                </Button>
              );
            })
          ) : (
            <div className="col-span-full">
              <Empty compact title="暂无可添加能力" description="请更换来源或搜索条件。" />
            </div>
          )}
        </div>
        <ModalFooter>
          <Button variant="secondary" onClick={close}>
            取消
          </Button>
          <Button disabled={!selected.length || submitting} onClick={() => void submit()}>
            添加{selected.length ? `（${selected.length}）` : ''}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
