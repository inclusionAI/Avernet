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
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">资源目录</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">
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
            variant="secondary"
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
                  <Button
                    variant="ghost"
                    className="-mx-2 -my-1.5 h-auto min-w-0 flex-1 justify-start gap-3 px-2 py-1.5 text-left font-normal"
                    aria-label={`预览${item.name}`}
                    disabled={desktop && (item.size ?? 0) > 1048576}
                    onClick={() => void onPreview(item.path).then((result) => setPreview({ path: item.path, result }))}
                  >
                    <File className="size-4 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{item.name}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {item.path}
                        {item.type === 'file' && item.size !== undefined ? ` · ${formatResourceBytes(item.size)}` : ''}
                      </div>
                    </div>
                  </Button>
                )}
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
            ))}
          </div>
        ) : (
          <Empty compact title="工作区为空" description="当前目录没有文件或文件夹。" />
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
