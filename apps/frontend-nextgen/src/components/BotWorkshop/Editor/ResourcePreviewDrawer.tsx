import { Button } from '@/components/ui/Button';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/Drawer';
import type { BotEditorResourcePreview } from '@/domain/botEditor';
import { useEffect, useState } from 'react';

export function ResourcePreviewDrawer({
  preview,
  onClose,
}: {
  preview?: { path: string; result: BotEditorResourcePreview };
  onClose: () => void;
}) {
  const [imageUrl, setImageUrl] = useState('');
  useEffect(() => {
    if (preview?.result.kind !== 'image') {
      setImageUrl('');
      return undefined;
    }
    const url = URL.createObjectURL(preview.result.blob);
    setImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [preview]);
  const fileName = preview?.path.split('/').pop() || '资源文件';
  return (
    <Drawer open={Boolean(preview)} onOpenChange={(open) => !open && onClose()}>
      <DrawerContent side="right" size="lg">
        <DrawerHeader className="border-b border-border pb-4">
          <DrawerTitle className="truncate text-base font-semibold">{fileName}</DrawerTitle>
          <DrawerDescription className="break-all text-xs text-muted-foreground">/{preview?.path}</DrawerDescription>
        </DrawerHeader>
        {preview?.result.kind === 'image' ? (
          imageUrl ? (
            <div className="flex min-h-64 items-center justify-center rounded-lg bg-muted/30 p-4">
              <img src={imageUrl} alt={fileName} className="max-h-[70vh] max-w-full object-contain" />
            </div>
          ) : null
        ) : preview ? (
          <pre className="m-0 min-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-muted/30 p-4 text-xs leading-6">
            {preview.result.content}
          </pre>
        ) : null}
        <DrawerFooter className="border-t border-border pt-4">
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
