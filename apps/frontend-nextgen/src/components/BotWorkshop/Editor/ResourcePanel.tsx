import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotEditorResource, BotEditorResourcePreview } from '@/domain/botEditor';
import { buildVisibleResourceTree } from '@/services/botWorkshop/resourceTree';
import { FolderPlus, Upload } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { ResourcePanelRow } from './ResourcePanelRow';
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
    <div className="flex min-h-full flex-col bg-background">
      <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-4">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold tracking-tight">资源目录</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">上传文件、新建目录，支持预览、下载与删除。</p>
        </div>
        <div className="flex max-w-full shrink-0 justify-end gap-2">
          {desktop && onOpenFolder ? (
            <Button variant="outline" size="md" onClick={() => void onOpenFolder(directory || undefined)}>
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
              // 失败提示已由 act 的 toast 负责，吞掉传播避免 dev 遮罩报 Unhandled Rejection
              if (file) void uploadToDirectory(target, file).catch(() => undefined);
              event.target.value = '';
            }}
          />
          <Button
            variant="outline"
            size="md"
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
            variant="outline"
            size="md"
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
      <div className="w-full px-4 pb-6 pt-3">
        {resources.length ? (
          <div>
            {visibleResources.map(({ item, depth }) => (
              <ResourcePanelRow
                key={item.path}
                item={item}
                depth={depth}
                isOpen={expanded.includes(item.path)}
                dirLoading={loadingPaths.includes(item.path)}
                selected={directory === item.path || preview?.path === item.path}
                editable={editable}
                desktop={desktop}
                onToggleDirectory={toggleDirectory}
                onAddFileTo={(target) => {
                  uploadDirectoryRef.current = target;
                  uploadRef.current?.click();
                }}
                onCreateSubdirectory={(parent) => {
                  setCreateParent(parent);
                  setCreateOpen(true);
                }}
                onPreviewFile={(target) =>
                  void onPreview(target)
                    .then((result) => setPreview({ path: target, result }))
                    .catch(() => undefined)
                }
                onCopyPath={onCopyPath}
                onDownload={onDownload}
                onDelete={onDelete}
              />
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card py-14">
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
                void onCreateDirectory(createParent ? `${createParent}/${path.trim()}` : path.trim())
                  .then(async () => {
                    if (createParent) await onLoadDirectory(createParent);
                    setPath('');
                    setCreateOpen(false);
                  })
                  .catch(() => undefined)
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