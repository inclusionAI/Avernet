import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotEditorResource } from '@/domain/botEditor';
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
import { useMemo, useRef, useState } from 'react';

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
  onPreview: (path: string) => Promise<string>;
  onDownload: (path: string, type: BotEditorResource['type']) => Promise<void>;
  onLoadDirectory: (path: string) => Promise<void>;
  loadingPaths: string[];
}) {
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<{ path: string; content: string }>();
  const [directory, setDirectory] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<string[]>([]);
  const uploadRef = useRef<HTMLInputElement>(null);
  const visibleResources = useMemo(() => buildVisibleResourceTree(resources, expanded), [expanded, resources]);
  return (
    <div className="p-5 sm:p-6">
      <Card>
        <CardHeader className="flex-wrap">
          <div className="min-w-0 flex-1">
            <CardTitle>容器文件目录</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
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
              disabled={!editable}
              leftIcon={<Upload className="size-4" />}
              onClick={() => uploadRef.current?.click()}
            >
              上传文件
            </Button>
            <Button
              disabled={!editable}
              leftIcon={<FolderPlus className="size-4" />}
              onClick={() => setCreateOpen(true)}
            >
              新建目录
            </Button>
          </div>
        </CardHeader>
        <CardContent>
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
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`${expanded.includes(item.path) ? '收起' : '展开'}${item.name}`}
                        leftIcon={
                          loadingPaths.includes(item.path) ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : expanded.includes(item.path) ? (
                            <ChevronDown className="size-4" />
                          ) : (
                            <ChevronRight className="size-4" />
                          )
                        }
                        onClick={() => {
                          const open = expanded.includes(item.path);
                          setDirectory(item.path);
                          if (open) {
                            setExpanded((current) => current.filter((path) => path !== item.path));
                            return;
                          }
                          setExpanded((current) => [...current, item.path]);
                          if (!loaded.includes(item.path)) {
                            void onLoadDirectory(item.path)
                              .then(() => setLoaded((current) => [...new Set([...current, item.path])]))
                              .catch(() => undefined);
                          }
                        }}
                      />
                      <Folder className="size-4 text-warning" />
                    </div>
                  ) : (
                    <File className="size-4 text-muted-foreground" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{item.name}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {item.path}
                      {item.type === 'file' && item.size !== undefined ? ` · ${formatBytes(item.size)}` : ''}
                    </div>
                  </div>
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
                      onClick={() =>
                        void onPreview(item.path).then((content) => setPreview({ path: item.path, content }))
                      }
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
              <pre className="m-0 max-h-72 overflow-auto whitespace-pre-wrap text-xs">{preview.content}</pre>
            </Card>
          ) : null}
        </CardContent>
      </Card>
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
