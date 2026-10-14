import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotEditorResource, BotEditorResourcePreview } from '@/domain/botEditor';
import { buildVisibleResourceTree, formatResourceBytes } from '@/services/botWorkshop/resourceTree';
import {
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  File,
  FilePlus,
  Folder,
  FolderPlus,
  Loader2,
  Trash2,
  Upload,
} from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { ResourcePreviewDrawer } from './ResourcePreviewDrawer';

export function ResourcePanel({
  resources,
  editable,
  desktop = false,
  onOpenFolder,
  onCreateDirectory,
  onDelete,
  onUpload,
  onPreview,
  onDownload,
  onCopyPath,
  onLoadDirectory,
  loadingPaths,
}: {
  resources: BotEditorResource[];
  editable: boolean;
  desktop?: boolean;
  onOpenFolder?: (path?: string) => Promise<void>;
  onCreateDirectory: (path: string) => Promise<void>;
  onDelete: (path: string) => Promise<void>;
  onUpload: (path: string, file: File) => Promise<void>;
  onPreview: (path: string) => Promise<BotEditorResourcePreview>;
  onDownload: (path: string, type: BotEditorResource['type']) => Promise<void>;
  onCopyPath: (path: string) => Promise<void>;
  onLoadDirectory: (path: string) => Promise<void>;
  loadingPaths: string[];
}) {
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<{ path: string; result: BotEditorResourcePreview }>();
  const [directory, setDirectory] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [createParent, setCreateParent] = useState('');
  const [expanded, setExpanded] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<string[]>([]);
  const uploadRef = useRef<HTMLInputElement>(null);
  const uploadDirectoryRef = useRef('');
  const visibleResources = useMemo(() => buildVisibleResourceTree(resources, expanded), [expanded, resources]);
  const resourceIconClassName = (item: BotEditorResource) => {
    if (item.type === 'folder') return 'text-warning';
    if (/\.json$/i.test(item.name)) return 'text-violet-500';
    if (/\.(md|mdx)$/i.test(item.name)) return 'text-emerald-500';
    return 'text-muted-foreground';
  };
  const uploadToDirectory = async (target: string, file: File) => {
    await onUpload(target ? `${target}/${file.name}` : file.name, file);
    if (target) await onLoadDirectory(target);
  };
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
    <div className="flex min-h-full flex-col bg-background">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border bg-card px-8 py-5 lg:px-10">
        <div>
          <h2 className="m-0 text-lg font-semibold tracking-tight">资源目录</h2>
          <p className="m-0 mt-1 text-sm text-muted-foreground">
            展示 Bot 工作区根目录；列表、建目录和删除均使用资源 OpenAPI。
          </p>
        </div>
        <div className="flex max-w-full shrink-0 flex-wrap justify-end gap-2">
          {desktop && onOpenFolder ? (
            <Button variant="outline" size="sm" onClick={() => void onOpenFolder(directory || undefined)}>
              打开本地目录
            </Button>
          ) : null}
          <Input
            ref={uploadRef}
            hidden
            type="file"
            aria-label="资源文件选择"
            onChange={(event) => {
              const file = event.target.files?.[0];
              const target = uploadDirectoryRef.current;
              if (file) void uploadToDirectory(target, file);
              event.target.value = '';
            }}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={!editable}
            leftIcon={<Upload className="size-4" />}
            onClick={() => {
              uploadDirectoryRef.current = directory;
              uploadRef.current?.click();
            }}
          >
            上传文件
          </Button>
          <Button
            size="sm"
            disabled={!editable}
            leftIcon={<FolderPlus className="size-4" />}
            onClick={() => {
              setCreateParent(directory);
              setCreateOpen(true);
            }}
          >
            新建目录
          </Button>
        </div>
      </div>
      <div className="mx-auto w-full max-w-5xl px-6 py-6 lg:px-10">
        <div className="mb-3 flex min-h-10 items-center rounded-lg border border-border bg-card px-3 text-xs text-muted-foreground">
          当前路径：/<span className="ml-1 font-medium text-foreground">{directory || '根目录'}</span>
          <span className="ml-1">（点击文件夹可切换上传位置）</span>
        </div>
        {resources.length ? (
          <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
            {visibleResources.map(({ item, depth }) => {
              const selected = directory === item.path || preview?.path === item.path;
              return (
                <div
                  key={item.path}
                  className={`group flex min-h-10 items-center gap-1 rounded-lg px-3 py-1 transition-colors ${
                    selected ? 'bg-primary/5' : 'hover:bg-muted/60'
                  }`}
                  style={{ paddingLeft: `${12 + depth * 24}px` }}
                >
                  {item.type === 'folder' ? (
                    <Button
                      variant="ghost"
                      className="-mx-2 -my-1 h-auto min-w-0 flex-1 shrink justify-start gap-1 rounded-md px-2 py-1 text-left font-normal hover:bg-transparent"
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
                      <Folder className={`ml-2 size-5 shrink-0 ${resourceIconClassName(item)}`} />
                      <span className="ml-2 min-w-0 flex-1 truncate text-sm font-medium">{item.name}</span>
                      <span className="sr-only">{item.path}</span>
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      className="-mx-2 -my-1 h-auto min-w-0 flex-1 justify-start gap-3 rounded-md px-2 py-1 text-left font-normal hover:bg-transparent"
                      aria-label={`预览${item.name}`}
                      disabled={desktop && (item.size ?? 0) > 1048576}
                      onClick={() => void onPreview(item.path).then((result) => setPreview({ path: item.path, result }))}
                    >
                      <File className={`size-5 shrink-0 ${resourceIconClassName(item)}`} />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">{item.name}</span>
                      <span className="sr-only">
                        {item.path}
                        {item.size !== undefined ? ` · ${formatResourceBytes(item.size)}` : ''}
                      </span>
                    </Button>
                  )}
                  <div
                    className={`flex shrink-0 items-center gap-0.5 transition-opacity ${
                      selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'
                    }`}
                  >
                    {item.type === 'folder' ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          disabled={!editable}
                          aria-label={`向${item.name}添加文件`}
                          leftIcon={<FilePlus className="size-4" />}
                          onClick={() => {
                            uploadDirectoryRef.current = item.path;
                            uploadRef.current?.click();
                          }}
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          disabled={!editable}
                          aria-label={`在${item.name}中新建子目录`}
                          leftIcon={<FolderPlus className="size-4" />}
                          onClick={() => {
                            setCreateParent(item.path);
                            setCreateOpen(true);
                          }}
                        />
                      </>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`复制路径${item.name}`}
                      leftIcon={<Copy className="size-4" />}
                      onClick={() => void onCopyPath(item.path)}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={desktop && item.type === 'folder'}
                      aria-label={`下载${item.type === 'folder' ? '文件夹' : '文件'}${item.name}`}
                      leftIcon={<Download className="size-4" />}
                      onClick={() => void onDownload(item.path, item.type)}
                    />
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
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card py-12">
            <Empty compact title="工作区为空" description="当前目录没有文件或文件夹。" />
          </div>
        )}
      </div>
      <ResourcePreviewDrawer preview={preview} onClose={() => setPreview(undefined)} />
      <Modal open={createOpen} onOpenChange={setCreateOpen}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>{createParent ? `在 ${createParent} 中新建子目录` : '新建目录'}</ModalTitle>
          </ModalHeader>
          <Input autoFocus value={path} onChange={(event) => setPath(event.target.value)} placeholder="输入目录名称" />
          <ModalFooter>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button
              disabled={!path.trim()}
              onClick={() =>
                void onCreateDirectory(createParent ? `${createParent}/${path.trim()}` : path.trim()).then(async () => {
                  if (createParent) await onLoadDirectory(createParent);
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
