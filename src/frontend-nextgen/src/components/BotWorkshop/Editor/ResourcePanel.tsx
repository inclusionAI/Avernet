import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotEditorResource, BotEditorResourcePreview } from '@/domain/botEditor';
import {
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  File,
  Folder,
  FolderPlus,
  Loader2,
  Trash2,
  Upload,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function buildVisibleResourceTree(resources: BotEditorResource[], expanded: string[]) {
  const children = new Map<string, BotEditorResource[]>();
  resources.forEach((item) => children.set(item.parentPath, [...(children.get(item.parentPath) ?? []), item]));
  const result: Array<{ item: BotEditorResource; depth: number }> = [];
  const visited = new Set<string>();
  function append(parentPath: string, depth: number) {
    (children.get(parentPath) ?? []).forEach((item) => {
      if (visited.has(item.path)) return;
      visited.add(item.path);
      result.push({ item, depth });
      if (item.type === 'folder' && expanded.includes(item.path)) append(item.path, depth + 1);
    });
  }
  append('', 0);
  return result;
}

export function ResourcePanel({
  resources,
  editable,
  onCreateDirectory,
  onDelete,
  onUpload,
  onPreview,
  onDownload,
  onLoadDirectory,
  loadingPaths,
}: {
  resources: BotEditorResource[];
  editable: boolean;
  onCreateDirectory: (path: string) => Promise<void>;
  onDelete: (path: string) => Promise<void>;
  onUpload: (path: string, file: File) => Promise<void>;
  onPreview: (path: string) => Promise<BotEditorResourcePreview>;
  onDownload: (path: string, type: BotEditorResource['type']) => Promise<void>;
  onLoadDirectory: (path: string) => Promise<void>;
  loadingPaths: string[];
}) {
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<{ path: string; result: BotEditorResourcePreview }>();
  const [previewImageUrl, setPreviewImageUrl] = useState('');
  const [directory, setDirectory] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<string[]>([]);
  const uploadRef = useRef<HTMLInputElement>(null);
  const visibleResources = useMemo(() => buildVisibleResourceTree(resources, expanded), [expanded, resources]);
  useEffect(() => {
    if (preview?.result.kind !== 'image') {
      setPreviewImageUrl('');
      return undefined;
    }
    const url = URL.createObjectURL(preview.result.blob);
    setPreviewImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [preview]);
  const toggleDirectory = (target: string) => {
    const open = expanded.includes(target);
    setDirectory(target);
    if (open) {
      setExpanded((current) => current.filter((path) => path !== target));
      return;
    }
    setExpanded((current) => [...current, target]);
    if (!loaded.includes(target)) {
      void onLoadDirectory(target)
        .then(() => setLoaded((current) => [...new Set([...current, target])]))
        .catch(() => undefined);
    }
  };
  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">资源目录</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">
            展示 Bot 工作区根目录；列表、建目录和删除均使用资源 OpenAPI。
          </p>
        </div>
        <div className="flex max-w-full shrink-0 flex-wrap justify-end gap-2">
          <Input
            ref={uploadRef}
            hidden
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void onUpload(directory ? `${directory}/${file.name}` : file.name, file);
              event.target.value = '';
            }}
          />
          <Button
            variant="secondary"
            size="sm"
            disabled={!editable}
            leftIcon={<Upload className="size-4" />}
            onClick={() => uploadRef.current?.click()}
          >
            上传文件
          </Button>
          <Button
            size="sm"
            disabled={!editable}
            leftIcon={<FolderPlus className="size-4" />}
            onClick={() => setCreateOpen(true)}
          >
            新建目录
          </Button>
        </div>
      </div>
      <div className="px-5 py-4">
        <div className="mb-3 text-xs text-muted-foreground">
          当前上传位置：/{directory || '根目录'}（点击目录可切换）
        </div>
        {resources.length ? (
          <div className="divide-y divide-border rounded-lg border border-border">
            {visibleResources.map(({ item, depth }) => (
              <div
                key={item.path}
                className="flex items-center gap-3 p-3"
                style={{ paddingLeft: `${12 + depth * 24}px` }}
              >
                {item.type === 'folder' ? (
                  // 热区 = 三角 + 文件夹图标 + 名称/路径整段（原仅三角 icon 可点）。
                  // 负 margin 抵消自身 padding，使布局占位与改造前逐像素一致：行高、缩进、
                  // 与右侧操作区的间距均不变，hover 背景向外扩张 8px/6px 作为可视热区反馈。
                  <Button
                    variant="ghost"
                    className="-mx-2 -my-1.5 h-auto min-w-0 flex-1 shrink justify-start gap-1 rounded-lg px-2 py-1.5 text-left font-normal"
                    aria-label={`${expanded.includes(item.path) ? '收起' : '展开'}${item.name}`}
                    aria-expanded={expanded.includes(item.path)}
                    onClick={() => toggleDirectory(item.path)}
                  >
                    {loadingPaths.includes(item.path) ? (
                      <Loader2 className="size-4 shrink-0 animate-spin" />
                    ) : expanded.includes(item.path) ? (
                      <ChevronDown className="size-4 shrink-0" />
                    ) : (
                      <ChevronRight className="size-4 shrink-0" />
                    )}
                    <Folder className="size-4 shrink-0 text-warning" />
                    <span className="ml-2 min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{item.name}</span>
                      <span className="block truncate text-xs text-muted-foreground">{item.path}</span>
                    </span>
                  </Button>
                ) : (
                  <>
                    <File className="size-4 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{item.name}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {item.path}
                        {item.type === 'file' && item.size !== undefined ? ` · ${formatBytes(item.size)}` : ''}
                      </div>
                    </div>
                  </>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`下载${item.type === 'folder' ? '文件夹' : '文件'}${item.name}`}
                  leftIcon={<Download className="size-4" />}
                  onClick={() => void onDownload(item.path, item.type)}
                />
                {item.type === 'file' ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`预览${item.name}`}
                    leftIcon={<Eye className="size-4" />}
                    onClick={() => void onPreview(item.path).then((result) => setPreview({ path: item.path, result }))}
                  />
                ) : null}
                <ConfirmDialog
                  title="删除资源"
                  description={`确认递归删除「${item.path}」？`}
                  confirmVariant="destructive"
                  onConfirm={() => onDelete(item.path)}
                  disabled={!editable}
                >
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`删除${item.name}`}
                    leftIcon={<Trash2 className="size-4" />}
                  />
                </ConfirmDialog>
              </div>
            ))}
          </div>
        ) : (
          <Empty compact title="工作区为空" description="当前目录没有文件或文件夹。" />
        )}
        {preview ? (
          <Card className="mt-4 bg-muted/30 p-4 shadow-none">
            <div className="mb-2 flex items-center justify-between">
              <p className="m-0 truncate text-xs font-medium">{preview.path}</p>
              <Button variant="ghost" size="sm" onClick={() => setPreview(undefined)}>
                关闭预览
              </Button>
            </div>
            {preview.result.kind === 'image' ? (
              previewImageUrl ? (
                <div className="flex max-h-[480px] justify-center overflow-auto rounded-lg bg-background p-3">
                  <img
                    src={previewImageUrl}
                    alt={preview.path.split('/').pop() || '资源图片预览'}
                    className="max-h-[440px] max-w-full object-contain"
                  />
                </div>
              ) : null
            ) : (
              <pre className="m-0 max-h-72 overflow-auto whitespace-pre-wrap text-xs">{preview.result.content}</pre>
            )}
          </Card>
        ) : null}
      </div>
      <Modal open={createOpen} onOpenChange={setCreateOpen}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>新建目录</ModalTitle>
          </ModalHeader>
          <Input autoFocus value={path} onChange={(event) => setPath(event.target.value)} placeholder="输入目录名称" />
          <ModalFooter>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button
              disabled={!path.trim()}
              onClick={() =>
                void onCreateDirectory(directory ? `${directory}/${path.trim()}` : path.trim()).then(() => {
                  setPath('');
                  setCreateOpen(false);
                })
              }
            >
              创建
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}
